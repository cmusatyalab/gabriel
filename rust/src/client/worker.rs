//! The client's thread. It owns the client's iceoryx2 ports, because they cannot move between
//! threads, and one flow router per flow. It takes commands from the [`Client`](super::Client)
//! handle over a channel.
//!
//! The thread sleeps until a producer publishes or the local server sends events, and also wakes
//! every [`COMMAND_POLL_INTERVAL`] for commands and deadlines. Each time, it applies the server's
//! events first, which can return tokens, then admits the producers' newest inputs, and carries
//! out what its routers decided. Every event about a producer's inputs is also published for the
//! producer's app.
//!
//! The connection to the local server is lost when the server's process stops, which the thread
//! checks every [`SERVER_CHECK_INTERVAL`], or when the server's stream of events loses some. Then
//! every offer on the server ends with `Disconnected`, and the thread installs its flows again as
//! soon as it can.
//!
//! Failures the thread carries on after are logged as warnings, with [`tracing`].

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
use tracing::{debug, error, info, info_span, warn};
use uuid::Uuid;

use crate::{
    Error, Result,
    client::{
        compile::compile,
        local_server::{LocalServer, ServerEvent},
        producers::{Producers, ReceivedInput},
    },
    ipc::types::{EngineName, EventKind, EventRecord, FlowName, Forward, PayloadType},
    naming,
    routing::{
        flow::{FlowRouter, RouterAction},
        ids::{EdgeId, NodeId, ProducerName},
        server_router::InstallFlow,
        tokens::{OfferEnd, Verdict},
    },
    services::{self, LOCAL_CROSSING_CAPACITY},
    spec::FlowSpec,
};

/// How long the thread waits for inputs and events before it checks for commands and deadlines
/// again. Bounds how late a command is handled and an offer times out.
const COMMAND_POLL_INTERVAL: Duration = Duration::from_millis(10);

/// How often the thread checks whether the local server's process stopped. Until then, the offers
/// on a stopped server stay open, and their tokens held, unless their deadlines pass first.
const SERVER_CHECK_INTERVAL: Duration = Duration::from_secs(1);

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
    /// Whether the flow is installed on the local server. Not while the connection is lost.
    connected: bool,
    /// The local server's part, kept to install it again after a lost connection.
    install: InstallFlow,
    /// The node of each engine, so an event names the node it is about.
    nodes: HashMap<EngineName, NodeId>,
    /// The target engines of each crossing, so the client can report what happened to a
    /// crossing's offer at each engine.
    crossings: HashMap<EdgeId, Vec<EngineName>>,
    producers: HashSet<ProducerName>,
}

impl ClientFlow {
    fn crossing_ids(&self) -> Vec<EdgeId> {
        self.crossings.keys().copied().collect()
    }
}

struct Worker {
    node: Node<ipc::Service>,
    producers: Producers,
    server: LocalServer,
    flows: HashMap<FlowName, ClientFlow>,
    next_server_check: Instant,
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
            // Every event the thread logs names the client.
            let _span = info_span!("client", name = %name).entered();
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
            let stopped = worker.run(&command_rx);
            if let Err(e) = &stopped {
                error!(error = %e, "client thread stopped");
            }
            stopped
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
            producers: Producers::default(),
            server,
            flows: HashMap::new(),
            next_server_check: Instant::now(),
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
                .producers
                .listeners()
                .chain(iter::once(self.server.events_ready()));
            services::wait_for_any(listeners, COMMAND_POLL_INTERVAL)?;
            let now = Instant::now();
            // Events can free tokens and crossings, so they go before new inputs. An event the
            // client cannot read leaves its offer open until the offer's deadline.
            match self.server.take_events() {
                Ok((events, lost)) => {
                    for event in &events {
                        self.apply(event, now);
                    }
                    // The lost events may have ended offers, so the client can no longer tell
                    // which of the server's offers are open.
                    if lost > 0 {
                        warn!(lost, "the local server's event stream lost events");
                        self.server_lost(now);
                    }
                }
                Err(e) => warn!(error = %e, "failed to read the local server's events"),
            }
            for input in self.producers.take_inputs() {
                self.admit(input, now);
            }
            for flow in self.flows.values_mut() {
                flow.router.expire(now);
            }
            if now >= self.next_server_check {
                self.check_server(now);
                self.next_server_check = now + SERVER_CHECK_INTERVAL;
            }
            self.carry_out();
            // A subscriber that is not woken still finds the events at its next wait.
            warn_on_error(
                self.producers.wake_subscribers(),
                "failed to wake a producer's subscribers",
            );
        }
    }

    fn handle(&mut self, command: Command) {
        // A caller that stopped waiting for its reply needs none, so failed replies are ignored.
        match command {
            Command::AddProducer { name, reply } => {
                let _ = reply.send(self.producers.add_producer(&self.node, &name));
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
        self.producers.remove_producer(name)
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
            if !self.producers.has_producer(&producer.name) {
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

        let engines: HashMap<NodeId, EngineName> = compiled
            .nodes
            .iter()
            .map(|(&engine, &node)| (node, engine))
            .collect();
        let crossings = compiled
            .local
            .crossings
            .iter()
            .map(|crossing| {
                let targets = crossing
                    .targets
                    .iter()
                    .map(|target| engines[&target.to])
                    .collect();
                (crossing.id, targets)
            })
            .collect();

        // Last, so a flow the client rejects is never installed.
        let session = self.server.install(compiled.local.clone())?;
        self.flows.insert(
            flow,
            ClientFlow {
                router,
                session,
                connected: true,
                install: compiled.local,
                nodes: compiled.nodes,
                crossings,
                producers,
            },
        );
        info!(flow = name, session, "added flow");
        Ok(())
    }

    /// Removes the flow `name` from the client and from the server. Its open tickets are dropped.
    fn remove_flow(&mut self, name: &str) -> Result<()> {
        let flow = FlowName::try_from(name)
            .ok()
            .and_then(|flow| self.flows.remove(&flow))
            .ok_or_else(|| Error::InvalidArgument(format!("flow {name:?} does not exist")))?;
        info!(flow = name, "removed flow");
        if !flow.connected {
            // After lost events the server may still have the flow; if the server is gone,
            // there is nothing to remove.
            let _ = self.server.remove_flow(flow.session);
            return Ok(());
        }
        self.server.remove_flow(flow.session)
    }

    /// Removes every flow from the server, so that a client that stops leaves nothing installed.
    fn remove_all_flows(&mut self) {
        for (_, flow) in self.flows.drain() {
            // The server may be gone already; then there is nothing to remove.
            let _ = self.server.remove_flow(flow.session);
        }
    }

    /// Applies an event from the server to its flow's router, and publishes it for the app of the
    /// input's producer, unless it is a duplicate. `Accepted` is not published: the app learns
    /// about each engine's offer from the events that follow.
    fn apply(&mut self, event: &ServerEvent, now: Instant) {
        let record = &event.record;
        let Some(flow) = self.flows.get_mut(&record.flow) else {
            // The flow was removed since.
            return;
        };
        let Some(&offer) = record.offer.as_option_ref() else {
            // A derived result answers no offer.
            return;
        };
        let children = || record.children.as_slice().to_vec();
        let end = match record.kind {
            EventKind::Accepted => OfferEnd::Accepted {
                children: children(),
            },
            EventKind::Result => {
                let Some(&node) = flow.nodes.get(&record.engine) else {
                    return;
                };
                OfferEnd::Result {
                    node,
                    forwarded: record.forward != Forward::Stop,
                    children: children(),
                }
            }
            EventKind::Skipped => OfferEnd::Skipped,
            EventKind::TimedOut => OfferEnd::TimedOut,
            // Sending ends no offer, but the app learns that a result may come.
            EventKind::Sent => {
                self.publish(record.clone(), &event.result);
                return;
            }
            // The client reports these itself, and the server answers no offer with them.
            EventKind::Disconnected | EventKind::Derived => return,
        };
        let published = record.kind != EventKind::Accepted;
        let verdict = flow.router.end_offer(&record.id, offer, end, now);
        if verdict == Verdict::Dropped {
            // A duplicate, an event for an offer that already ended, e.g. with a lost connection,
            // or one for an offer the client never made.
            debug!(kind = ?record.kind, ?offer, input = ?record.id, "dropped an unexpected event");
        } else if published {
            self.publish(record.clone(), &event.result);
        }
    }

    /// Publishes `record` for the app of its input's producer. An event the app misses only
    /// leaves it waiting, so the thread carries on after a failure.
    fn publish(&mut self, record: EventRecord, result: &[u8]) {
        let producer = record.id.producer;
        warn_on_error(
            self.producers.publish(&producer, record, result),
            "failed to publish an event for a producer's app",
        );
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

    /// Checks whether the local server's process stopped. If it did, or if it is gone while flows
    /// are installed on it, the connection is lost. Otherwise, flows whose connection was lost
    /// are installed again.
    fn check_server(&mut self, now: Instant) {
        let stopped = match self.server.check(&self.node) {
            Ok(stopped) => stopped,
            Err(e) => {
                warn!(error = %e, "failed to check the local server");
                return;
            }
        };
        let installed = self.flows.values().any(|flow| flow.connected);
        if stopped || (installed && !self.server.is_running()) {
            warn!("the local server stopped or restarted");
            self.server_lost(now);
        } else {
            self.reconnect(now);
        }
    }

    /// The connection to the local server was lost. Every offer of every flow on the server ends
    /// with `Disconnected`, and each flow's producers' apps are told once. Then the flows are
    /// installed again, if the server can be reached.
    fn server_lost(&mut self, now: Instant) {
        for (&name, flow) in self.flows.iter_mut().filter(|(_, flow)| flow.connected) {
            flow.connected = false;
            flow.router
                .disconnect(flow.session, &flow.crossing_ids(), now);
            for producer in &flow.producers {
                let record = EventRecord::disconnected(name);
                warn_on_error(
                    self.producers.publish(producer, record, &[]),
                    "failed to publish an event for a producer's app",
                );
            }
        }
        self.reconnect(now);
    }

    /// Installs every flow whose connection was lost again, and lets it send again. A flow the
    /// server refuses stays disconnected until the next try.
    fn reconnect(&mut self, now: Instant) {
        for (name, flow) in self.flows.iter_mut().filter(|(_, flow)| !flow.connected) {
            // After lost events the server still has the flow, so it is removed first. If the
            // server restarted, it has nothing to remove.
            let _ = self.server.remove_flow(flow.session);
            let session = match self.server.install(flow.install.clone()) {
                Ok(session) => session,
                Err(e) => {
                    // Tried again at every check, so not worth a warning each time.
                    debug!(flow = name.as_str(), error = %e, "failed to install flow again");
                    continue;
                }
            };
            info!(flow = name.as_str(), session, "installed flow again");
            flow.session = session;
            flow.connected = true;
            flow.router.reconnect(&flow.crossing_ids(), now);
        }
    }

    /// Carries out what the routers decided: sends inputs on their crossings, and reports offers
    /// that ended on the client. Every client edge is a crossing to the local server for now.
    fn carry_out(&mut self) {
        for (&name, flow) in &mut self.flows {
            while let Some(action) = flow.router.poll_action() {
                match action {
                    RouterAction::Send {
                        offer,
                        input_id,
                        input,
                        ..
                    } => {
                        // An input the server did not get times out on its crossing, which frees
                        // the crossing and, later, the input's token. Producers set no payload
                        // type yet.
                        warn_on_error(
                            self.server.send(
                                flow.session,
                                offer,
                                input_id,
                                PayloadType::new(),
                                &input,
                            ),
                            "failed to send an input to the local server",
                        );
                    }
                    RouterAction::Skipped {
                        edge,
                        offer,
                        input_id,
                    } => {
                        for &engine in &flow.crossings[&edge] {
                            let record = EventRecord::skipped(offer, input_id, engine, name);
                            warn_on_error(
                                self.producers.publish(&input_id.producer, record, &[]),
                                "failed to publish an event for a producer's app",
                            );
                        }
                        // The engines' subscribers learn it from the server. A notice that does
                        // not arrive only leaves them waiting.
                        if flow.connected {
                            warn_on_error(
                                self.server.send_skipped(flow.session, offer, input_id),
                                "failed to send a skip notice to the local server",
                            );
                        }
                    }
                    RouterAction::TimedOut {
                        edge,
                        offer,
                        input_id,
                    } => {
                        for &engine in &flow.crossings[&edge] {
                            let record = EventRecord::timed_out(offer, input_id, engine, name);
                            warn_on_error(
                                self.producers.publish(&input_id.producer, record, &[]),
                                "failed to publish an event for a producer's app",
                            );
                        }
                    }
                }
            }
        }
    }
}

/// Logs the error of `result`, a failure the thread carries on after, as a warning about `what`.
fn warn_on_error(result: Result<()>, what: &str) {
    if let Err(e) = result {
        warn!(error = %e, "{what}");
    }
}
