//! The client's thread. It owns the client's iceoryx2 ports, because they cannot move between
//! threads, and one flow router per flow. It takes commands from the [`Client`](super::Client)
//! handle over a channel.
//!
//! The thread sleeps until a producer publishes or the local server sends events, and also wakes
//! every [`COMMAND_POLL_INTERVAL`] for commands and deadlines. Each time, it applies the server's
//! events first, which can return tokens, then admits the producers' newest inputs, and sends
//! what its routers decided.

use std::{
    collections::{HashMap, HashSet},
    iter,
    sync::{
        Arc,
        mpsc::{self, TryRecvError},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use iceoryx2::prelude::*;
use iceoryx2_bb_container::{string::String as _, vector::Vector as _};
use uuid::Uuid;

use crate::{
    Error, Result,
    client::{
        compile::compile,
        local_server::LocalServer,
        receiver::{InputReceiver, ReceivedInput},
    },
    ipc::types::{EngineName, EventKind, EventRecord, FlowName, Forward, PayloadType},
    naming,
    routing::{
        flow::{FlowRouter, RouterAction},
        ids::{NodeId, ProducerName},
        tokens::OfferEnd,
    },
    services::{self, LOCAL_CROSSING_CAPACITY},
    spec::FlowSpec,
};

/// How long the thread waits for inputs and events before it checks for commands and deadlines
/// again. Bounds how late a command is handled and an offer times out.
const COMMAND_POLL_INTERVAL: Duration = Duration::from_millis(10);

pub(super) enum Command {
    AddProducer {
        name: String,
        reply: mpsc::Sender<Result<()>>,
    },
    RemoveProducer {
        name: String,
        reply: mpsc::Sender<Result<()>>,
    },
    AddFlow {
        name: String,
        spec: FlowSpec,
        reply: mpsc::Sender<Result<()>>,
    },
    RemoveFlow {
        name: String,
        reply: mpsc::Sender<Result<()>>,
    },
}

/// One flow the client added.
struct ClientFlow {
    /// Producer inputs are kept as shared bytes; the router clones them for each crossing.
    router: FlowRouter<Arc<[u8]>>,
    /// The session the local server gave the flow.
    session: u64,
    /// The node of each engine, so an event names the node it is about.
    nodes: HashMap<EngineName, NodeId>,
    producers: HashSet<ProducerName>,
}

struct Worker {
    node: Node<ipc::Service>,
    receiver: InputReceiver,
    server: LocalServer,
    flows: HashMap<FlowName, ClientFlow>,
}

/// Starts the thread of the client `name`, which talks to the local server `local_server`, and
/// returns its command channel once the thread has set up its iceoryx2 ports.
pub(super) fn spawn(
    name: &str,
    local_server: &str,
) -> Result<(mpsc::Sender<Command>, JoinHandle<Result<()>>)> {
    let (started_tx, started_rx) = mpsc::channel();
    let (command_tx, command_rx) = mpsc::channel();
    let (name, local_server) = (name.to_owned(), local_server.to_owned());
    let thread = thread::Builder::new()
        .name(format!("gabriel-client-{name}"))
        .spawn(move || {
            // Created inside the thread: iceoryx2 ports cannot be moved between threads.
            let worker = match Worker::new(&name, &local_server) {
                Ok(worker) => {
                    let _ = started_tx.send(Ok(()));
                    worker
                }
                Err(e) => {
                    let _ = started_tx.send(Err(e));
                    return Ok(());
                }
            };
            worker.run(&command_rx)
        })
        .map_err(|e| Error::Internal(format!("failed to spawn client thread: {e}")))?;
    match started_rx.recv() {
        Ok(Ok(())) => Ok((command_tx, thread)),
        Ok(Err(e)) => {
            let _ = thread.join();
            Err(e)
        }
        Err(_) => {
            let _ = thread.join();
            Err(Error::Internal(String::from(
                "client thread exited during startup",
            )))
        }
    }
}

impl Worker {
    fn new(name: &str, local_server: &str) -> Result<Self> {
        let node = NodeBuilder::new()
            .name(&naming::client_node(name)?)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        let server = LocalServer::open(&node, name, local_server)?;
        Ok(Self {
            node,
            receiver: InputReceiver::default(),
            server,
            flows: HashMap::new(),
        })
    }

    /// Runs until the command channel closes, then removes the client's flows from the server.
    /// Returns an error only if the thread cannot wait any more.
    fn run(mut self, commands: &mpsc::Receiver<Command>) -> Result<()> {
        loop {
            loop {
                match commands.try_recv() {
                    Ok(command) => self.handle(command),
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        self.remove_all_flows();
                        return Ok(());
                    }
                }
            }

            let listeners = self
                .receiver
                .listeners()
                .chain(iter::once(self.server.events_ready()));
            services::wait_for_any(listeners, COMMAND_POLL_INTERVAL)?;
            let now = Instant::now();
            // Events can free tokens and crossings, so they go before new inputs. An event the
            // client cannot read leaves its offer open until the offer's deadline.
            if let Ok(events) = self.server.take_events() {
                for event in events {
                    self.apply(&event, now);
                }
            }
            for input in self.receiver.take_inputs() {
                self.admit(input, now);
            }
            for flow in self.flows.values_mut() {
                flow.router.expire(now);
            }
            self.send_inputs();
        }
    }

    fn handle(&mut self, command: Command) {
        // A caller that stopped waiting for its reply needs none, so failed replies are ignored.
        match command {
            Command::AddProducer { name, reply } => {
                let _ = reply.send(self.receiver.add_producer(&self.node, &name));
            }
            Command::RemoveProducer { name, reply } => {
                let _ = reply.send(self.remove_producer(&name));
            }
            Command::AddFlow { name, spec, reply } => {
                let _ = reply.send(self.add_flow(&name, &spec));
            }
            Command::RemoveFlow { name, reply } => {
                let _ = reply.send(self.remove_flow(&name));
            }
        }
    }

    /// Stops taking inputs from the producer `name`. Fails while a flow uses it.
    fn remove_producer(&mut self, name: &str) -> Result<()> {
        let in_use = ProducerName::try_from(name).is_ok_and(|producer| {
            self.flows
                .values()
                .any(|flow| flow.producers.contains(&producer))
        });
        if in_use {
            return Err(Error::InvalidArgument(format!(
                "producer {name:?} is used by a flow"
            )));
        }
        self.receiver.remove_producer(name)
    }

    /// Checks the flow `name`, installs the local server's part, and starts routing the client's
    /// part.
    fn add_flow(&mut self, name: &str, spec: &FlowSpec) -> Result<()> {
        let flow = FlowName::try_from(name)
            .ok()
            .filter(|_| !name.is_empty())
            .ok_or_else(|| Error::InvalidArgument(format!("invalid flow name {name:?}")))?;
        if self.flows.contains_key(&flow) {
            return Err(Error::InvalidArgument(format!(
                "flow {name:?} already exists"
            )));
        }
        let compiled = compile(name, spec)?;

        // A random session keeps the client's offer ids from before a restart apart from new ones.
        let (session, _) = Uuid::new_v4().as_u64_pair();
        let mut router = FlowRouter::new(session);
        let mut producers = HashSet::new();
        for producer in &compiled.producers {
            if !self.receiver.has_producer(&producer.name) {
                return Err(Error::InvalidArgument(format!(
                    "producer {:?} is not added to the client",
                    producer.name
                )));
            }
            router.add_producer(
                producer.name.as_str(),
                producer.tokens,
                producer.ticket_timeout,
            )?;
            producers.insert(producer.name);
        }
        for crossing in compiled.crossings {
            router.add_edge(
                crossing.id,
                crossing.source,
                crossing.target,
                LOCAL_CROSSING_CAPACITY,
                crossing.timeout,
            )?;
        }

        // Last, so a flow the client rejects is never installed.
        let session = self.server.install(compiled.local)?;
        self.flows.insert(
            flow,
            ClientFlow {
                router,
                session,
                nodes: compiled.nodes,
                producers,
            },
        );
        Ok(())
    }

    /// Removes the flow `name` from the client and from the server. Its open tickets are dropped.
    fn remove_flow(&mut self, name: &str) -> Result<()> {
        let flow = FlowName::try_from(name)
            .ok()
            .and_then(|flow| self.flows.remove(&flow))
            .ok_or_else(|| Error::InvalidArgument(format!("flow {name:?} does not exist")))?;
        self.server.remove_flow(flow.session)
    }

    /// Removes every flow from the server, so that a client that stops leaves nothing installed.
    fn remove_all_flows(&mut self) {
        for (_, flow) in self.flows.drain() {
            // The server may be gone already; then there is nothing to remove.
            let _ = self.server.remove_flow(flow.session);
        }
    }

    /// Applies an event from the server to its flow's router.
    fn apply(&mut self, event: &EventRecord, now: Instant) {
        let Some(flow) = self.flows.get_mut(&event.flow) else {
            // The flow was removed since.
            return;
        };
        let Some(&offer) = event.offer.as_option_ref() else {
            // A derived result answers no offer.
            return;
        };
        let children = || event.children.as_slice().to_vec();
        let end = match event.kind {
            EventKind::Accepted => OfferEnd::Accepted {
                children: children(),
            },
            EventKind::Result => {
                let Some(&node) = flow.nodes.get(&event.engine) else {
                    return;
                };
                OfferEnd::Result {
                    node,
                    forwarded: event.forward != Forward::Stop,
                    children: children(),
                }
            }
            EventKind::Skipped => OfferEnd::Skipped,
            EventKind::TimedOut => OfferEnd::TimedOut,
            // Sending ends no offer, and a derived result answers none.
            EventKind::Sent | EventKind::Derived => return,
        };
        flow.router.end_offer(&event.id, offer, end, now);
    }

    /// Hands a producer's newest input to the flow it was published on.
    fn admit(&mut self, input: ReceivedInput, now: Instant) {
        let Some(flow) = self.flows.get_mut(&input.flow) else {
            // No flow of that name was added: nothing consumes the input.
            return;
        };
        if flow.producers.contains(&input.id.producer) {
            flow.router
                .input_ready(input.id, input.data, now)
                .expect("the flow's producers are in its router");
        }
    }

    /// Sends the inputs the routers decided to send. Every client edge is a crossing to the local
    /// server for now.
    fn send_inputs(&mut self) {
        for flow in self.flows.values_mut() {
            while let Some(RouterAction::Send {
                offer,
                input_id,
                input,
                ..
            }) = flow.router.poll_action()
            {
                // An input the server did not get times out on its crossing, which frees the
                // crossing and, later, the input's token. Producers set no payload type yet.
                let _ = self
                    .server
                    .send(flow.session, offer, input_id, PayloadType::new(), &input);
            }
        }
    }
}
