//! The client's thread. It owns the client's iceoryx2 ports, because they cannot move between
//! threads, and one flow router per flow. It takes commands from the [`Client`](super::Client)
//! handle over a channel.
//!
//! The thread sleeps until a producer publishes or a server sends events, and also wakes every
//! [`COMMAND_POLL_INTERVAL`] for commands and deadlines. Each time, it applies the servers' events
//! first, which can return tokens, then admits the producers' newest inputs, and carries out what
//! its routers decided. Every event about a producer's inputs is also published for the
//! producer's app.
//!
//! Each flow has a part on each server that runs some of its engines. The connection to a server
//! is lost when its process stops or its stream of events loses some (the local server), or when
//! the network connection drops (the remote server). Then every offer on that server ends with
//! `Disconnected`, and the thread installs the flows' parts again as soon as it can. A flow's
//! remote part is also installed late if the remote server was not connected when the flow was
//! added; until then, the flow's crossings to it send nothing.
//!
//! The events of the remote engines are also published on the device, under the engines' names,
//! so device processes can subscribe to them. Each relayed engine gets the events a producer's
//! app gets about it, and `Disconnected` when the remote server is lost.
//!
//! Failures the thread carries on after are logged as warnings, with [`tracing`].

use std::{
    collections::{HashMap, HashSet},
    sync::mpsc::{self, TryRecvError},
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
        RemoteServerConfig,
        compile::compile,
        local_server::LocalServer,
        producers::{Producers, ReceivedInput},
        relays::Relays,
        remote_server::RemoteServer,
        server_link::{ServerEvent, ServerLink},
    },
    ipc::types::{Data, EngineName, EventKind, EventRecord, FlowName, Forward},
    naming,
    routing::{
        flow::{FlowRouter, RouterAction},
        ids::{EdgeId, NodeId, ProducerName},
        server_router::InstallFlow,
        tokens::{OfferEnd, Verdict},
    },
    services::{self, LOCAL_CROSSING_CAPACITY},
    spec::{FlowSpec, Location},
};

/// How long the thread waits for inputs and events before it checks for commands and deadlines
/// again. Bounds how late a command is handled and an offer times out.
const COMMAND_POLL_INTERVAL: Duration = Duration::from_millis(10);

/// How often the thread checks whether a server stopped, and installs parts of flows that are not
/// installed. Until then, the offers on a stopped local server stay open, and their tokens held,
/// unless their deadlines pass first.
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

/// The part of a flow that one server runs.
struct Part {
    /// Kept to install the part again after a lost connection.
    install: InstallFlow,
    /// The session the server gave the part, once it was installed.
    session: Option<u64>,
    /// Whether the part is installed. Not before the first install, nor while the connection is
    /// lost.
    connected: bool,
    /// The client's crossings to the server.
    crossings: Vec<EdgeId>,
}

/// Where a crossing goes.
struct Crossing {
    location: Location,
    /// Its target engines, so the client can report what happened to a crossing's offer at each.
    targets: Vec<EngineName>,
}

/// One flow the client added.
struct ClientFlow {
    /// Producer inputs are kept as shared bytes; the router clones them for each crossing.
    router: FlowRouter<Data>,
    parts: HashMap<Location, Part>,
    /// The node of each engine, so an event names the node it is about.
    nodes: HashMap<EngineName, NodeId>,
    crossings: HashMap<EdgeId, Crossing>,
    producers: HashSet<ProducerName>,
    /// The flow's engines on the remote server, whose events the client relays.
    remote: HashSet<EngineName>,
}

struct Worker {
    node: Node<ipc::Service>,
    producers: Producers,
    relays: Relays,
    servers: HashMap<Location, Box<dyn ServerLink>>,
    /// How many inputs each crossing to the remote server may have in flight.
    remote_crossing_capacity: usize,
    flows: HashMap<FlowName, ClientFlow>,
    next_server_check: Instant,
}

/// Starts the thread of the client `name`, which talks to the local server `local_server`, and to
/// the remote server `remote` if it is given. Returns its command channel once the thread has set
/// up its ports.
pub(super) fn spawn(
    name: &str,
    local_server: &str,
    remote: Option<RemoteServerConfig>,
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
            let worker = match Worker::new(&name, &local_server, remote) {
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
    fn new(name: &str, local_server: &str, remote: Option<RemoteServerConfig>) -> Result<Self> {
        let node = NodeBuilder::new()
            .name(&naming::client_node(name)?)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        let mut servers: HashMap<Location, Box<dyn ServerLink>> = HashMap::from([(
            Location::Local,
            Box::new(LocalServer::open(&node, name, local_server)?) as Box<dyn ServerLink>,
        )]);
        let mut remote_crossing_capacity = LOCAL_CROSSING_CAPACITY;
        if let Some(remote) = remote {
            let server = RemoteServer::open(&node, name, remote.addr, &remote.certificate)?;
            servers.insert(Location::Remote, Box::new(server));
            remote_crossing_capacity = remote.crossing_capacity;
        }
        Ok(Self {
            node,
            producers: Producers::default(),
            relays: Relays::default(),
            servers,
            remote_crossing_capacity,
            flows: HashMap::new(),
            next_server_check: Instant::now(),
        })
    }

    /// Runs until the command channel closes, then removes the client's flows from the servers.
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
                .chain(self.servers.values().map(|server| server.ready()));
            services::wait_for_any(listeners, COMMAND_POLL_INTERVAL)?;
            let now = Instant::now();
            // Events can free tokens and crossings, so they go before new inputs. An event the
            // client cannot read leaves its offer open until the offer's deadline.
            let locations: Vec<Location> = self.servers.keys().copied().collect();
            for location in locations {
                self.take_events(location, now);
            }
            for input in self.producers.take_inputs() {
                self.admit(input, now);
            }
            for flow in self.flows.values_mut() {
                flow.router.expire(now);
            }
            if now >= self.next_server_check {
                self.check_servers(now);
                self.next_server_check = now + SERVER_CHECK_INTERVAL;
            }
            self.carry_out();
            // A subscriber that is not woken still finds the events at its next wait.
            warn_on_error(
                self.producers.wake_subscribers(),
                "failed to wake a producer's subscribers",
            );
            warn_on_error(
                self.relays.wake_subscribers(),
                "failed to wake a relayed engine's subscribers",
            );
        }
    }

    /// Applies what the server at `location` sent. A server that just connected gets the parts
    /// of flows that wait for it.
    fn take_events(&mut self, location: Location, now: Instant) {
        let server = self
            .servers
            .get_mut(&location)
            .expect("only servers the client has are asked");
        let was_connected = server.is_connected();
        let received = match server.take_events() {
            Ok(received) => received,
            Err(e) => {
                warn!(error = %e, ?location, "failed to read a server's events");
                return;
            }
        };
        let connected = server.is_connected();
        for event in &received.events {
            self.apply(location, event, now);
        }
        // The lost events may have ended offers, so the client can no longer tell which of the
        // server's offers are open.
        if received.lost {
            warn!(?location, "lost the connection to a server");
            self.server_lost(location, now);
        } else if connected && !was_connected {
            self.reconnect(location, now);
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

    /// Checks the flow `name`, installs each server's part, and starts routing the client's part.
    /// The local part must install. The remote part is installed now if the remote server is
    /// connected, and must then install too; otherwise it is installed once the server connects.
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
        let compiled = compile(name, spec, self.remote_crossing_capacity)?;
        if let Some(location) = compiled
            .parts
            .keys()
            .find(|location| !self.servers.contains_key(location))
        {
            return Err(Error::InvalidArgument(format!(
                "flow {name:?} has engines on the {location:?} server, which the client does not \
                 connect to"
            )));
        }

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
        for crossing in &compiled.crossings {
            let capacity = match crossing.location {
                Location::Local => LOCAL_CROSSING_CAPACITY,
                Location::Remote => self.remote_crossing_capacity,
            };
            router.add_edge(
                crossing.id,
                crossing.source.clone(),
                crossing.target,
                capacity,
                crossing.timeout,
            )?;
        }

        let engines: HashMap<NodeId, EngineName> = compiled
            .nodes
            .iter()
            .map(|(&engine, &node)| (node, engine))
            .collect();
        let remote: HashSet<EngineName> = compiled
            .parts
            .get(&Location::Remote)
            .map_or_else(HashSet::new, |part| {
                part.engines.iter().map(|(node, _)| engines[node]).collect()
            });
        if let Some(engine) = self.moved_engine(&compiled.nodes, &remote) {
            return Err(Error::InvalidArgument(format!(
                "engine {:?} is local in one flow and remote in another",
                engine.as_str()
            )));
        }
        let mut crossings = HashMap::new();
        let mut parts = HashMap::new();
        for (location, install) in compiled.parts {
            for crossing in &install.crossings {
                let targets = crossing
                    .targets
                    .iter()
                    .map(|target| engines[&target.to])
                    .collect();
                crossings.insert(crossing.id, Crossing { location, targets });
            }
            let part = Part {
                crossings: install
                    .crossings
                    .iter()
                    .map(|crossing| crossing.id)
                    .collect(),
                install,
                session: None,
                connected: false,
            };
            // Nothing is sent to a part until it is installed.
            router.suspend(&part.crossings);
            parts.insert(location, part);
        }
        let mut flow_state = ClientFlow {
            router,
            parts,
            nodes: compiled.nodes,
            crossings,
            producers,
            remote,
        };

        // Last, so a flow the client rejects is never installed.
        let now = Instant::now();
        let mut installed = Vec::new();
        for location in [Location::Local, Location::Remote] {
            let Some(part) = flow_state.parts.get_mut(&location) else {
                continue;
            };
            let server = &self.servers[&location];
            if location == Location::Remote && !server.is_connected() {
                info!(
                    flow = name,
                    "the remote server is not connected; its part waits"
                );
                continue;
            }
            match install(server.as_ref(), part, &mut flow_state.router, now) {
                Ok(()) => installed.push(location),
                Err(e) => {
                    // No flow is left half installed.
                    for location in installed {
                        if let Some(session) = flow_state.parts[&location].session {
                            let _ = self.servers[&location].remove_flow(session);
                        }
                    }
                    return Err(e);
                }
            }
        }
        self.relays.add(flow_state.remote.iter().copied());
        self.flows.insert(flow, flow_state);
        info!(flow = name, "added flow");
        Ok(())
    }

    /// An engine of a new flow, with the engines `nodes` of which `remote` are remote, that is on
    /// the other server in a flow the client has. Its events could not be told apart on the
    /// device.
    fn moved_engine(
        &self,
        nodes: &HashMap<EngineName, NodeId>,
        remote: &HashSet<EngineName>,
    ) -> Option<EngineName> {
        nodes.keys().copied().find(|engine| {
            self.flows.values().any(|flow| {
                flow.nodes.contains_key(engine)
                    && flow.remote.contains(engine) != remote.contains(engine)
            })
        })
    }

    /// Removes the flow `name` from the client and from the servers. Its open tickets are dropped.
    fn remove_flow(&mut self, name: &str) -> Result<()> {
        let flow = FlowName::try_from(name)
            .ok()
            .and_then(|flow| self.flows.remove(&flow))
            .ok_or_else(|| Error::InvalidArgument(format!("flow {name:?} does not exist")))?;
        info!(flow = name, "removed flow");
        self.relays.remove(flow.remote.iter().copied());
        let mut first_error = None;
        for (location, part) in &flow.parts {
            let Some(session) = part.session else {
                continue;
            };
            let removed = self.servers[location].remove_flow(session);
            // After lost events the server may still have the part; if the server is gone, there
            // is nothing to remove. Only a connected part must be removed.
            if part.connected
                && let Err(e) = removed
            {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    /// Removes every flow from the servers, so that a client that stops leaves nothing installed.
    fn remove_all_flows(&mut self) {
        for (_, flow) in self.flows.drain() {
            self.relays.remove(flow.remote.iter().copied());
            for (location, part) in &flow.parts {
                // The server may be gone already; then there is nothing to remove.
                if let Some(session) = part.session {
                    let _ = self.servers[location].remove_flow(session);
                }
            }
        }
    }

    /// Applies an event from the server at `location` to its flow's router, and publishes it for
    /// the app of the input's producer, unless it is a duplicate. `Accepted` is not published: the
    /// app learns about each engine's offer from the events that follow. A derived result answers
    /// no offer, and is only relayed on the device if it comes from the remote server; the local
    /// server published it here already.
    fn apply(&mut self, location: Location, event: &ServerEvent, now: Instant) {
        let record = &event.record;
        if record.kind == EventKind::Derived {
            if location == Location::Remote {
                warn_on_error(
                    self.relays
                        .publish_derived(&self.node, record, &event.result),
                    "failed to publish an event for a relayed engine",
                );
            }
            return;
        }
        let Some(flow) = self.flows.get_mut(&record.flow) else {
            // The flow was removed since.
            return;
        };
        let Some(&offer) = record.offer.as_option_ref() else {
            // Only a derived result answers no offer.
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
                // After an `Emit`, the server sends the new input if the client has edges leaving
                // the engine, and the client offers it on them.
                OfferEnd::Result {
                    node,
                    forwarded: record.forward == Forward::Pass
                        || (record.forward == Forward::Emit && event.forwarded.is_some()),
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
            // The client reports `Disconnected` itself, and relays `Derived` above.
            EventKind::Disconnected | EventKind::Derived => return,
        };
        let published = record.kind != EventKind::Accepted;
        let verdict = flow
            .router
            .end_offer(&record.id, offer, end, event.forwarded.clone(), now);
        if verdict == Verdict::Dropped {
            // A duplicate, an event for an offer that already ended, e.g. with a lost connection,
            // or one for an offer the client never made.
            debug!(kind = ?record.kind, ?offer, input = ?record.id, "dropped an unexpected event");
        } else if published {
            self.publish(record.clone(), &event.result);
        }
    }

    /// Publishes `record` for the app of its input's producer, and on its engine's service if the
    /// engine is relayed.
    fn publish(&mut self, record: EventRecord, result: &[u8]) {
        publish(
            &self.node,
            &mut self.producers,
            &mut self.relays,
            record,
            result,
        );
    }

    /// Hands a producer's newest input to the flow it was published on.
    fn admit(&mut self, input: ReceivedInput, now: Instant) {
        let Some(flow) = self.flows.get_mut(&input.flow) else {
            // No flow of that name was added: nothing consumes the input.
            return;
        };
        if flow.producers.contains(&input.id.producer) {
            let data = Data {
                bytes: input.data,
                payload_type: input.payload_type,
            };
            flow.router
                .input_ready(input.id, data, now)
                .expect("the flow's producers are in its router");
        }
    }

    /// Checks whether each server stopped. If one did, or if it is gone while parts are installed
    /// on it, the connection is lost. Otherwise, parts that are not installed are installed.
    fn check_servers(&mut self, now: Instant) {
        let locations: Vec<Location> = self.servers.keys().copied().collect();
        for location in locations {
            let server = self
                .servers
                .get_mut(&location)
                .expect("only servers the client has are checked");
            let stopped = match server.check() {
                Ok(stopped) => stopped,
                Err(e) => {
                    warn!(error = %e, ?location, "failed to check a server");
                    continue;
                }
            };
            let installed = self
                .flows
                .values()
                .any(|flow| flow.parts.get(&location).is_some_and(|part| part.connected));
            if stopped || (installed && !server.is_connected()) {
                warn!(?location, "a server stopped or restarted");
                self.server_lost(location, now);
            } else {
                self.reconnect(location, now);
            }
        }
    }

    /// The connection to the server at `location` was lost. Every offer of every flow on the
    /// server ends with `Disconnected`, and each flow's producers' apps are told once. Then the
    /// parts are installed again, if the server can be reached.
    fn server_lost(&mut self, location: Location, now: Instant) {
        for (&name, flow) in &mut self.flows {
            let Some(part) = flow.parts.get_mut(&location).filter(|part| part.connected) else {
                continue;
            };
            part.connected = false;
            let session = part.session.expect("a connected part was installed");
            flow.router.disconnect(session, &part.crossings, now);
            for producer in &flow.producers {
                let record = EventRecord::disconnected(name);
                warn_on_error(
                    self.producers.publish(producer, record, &[]),
                    "failed to publish an event for a producer's app",
                );
            }
            // The subscribers of the remote engines learn it too, under each engine's name.
            if location == Location::Remote {
                for &engine in &flow.remote {
                    let mut record = EventRecord::disconnected(name);
                    record.engine = engine;
                    warn_on_error(
                        self.relays.publish(&self.node, &record, &[]),
                        "failed to publish an event for a relayed engine",
                    );
                }
            }
        }
        self.reconnect(location, now);
    }

    /// Installs every part on the server at `location` that is not installed, and lets its
    /// crossings send. A part the server refuses stays waiting until the next try.
    fn reconnect(&mut self, location: Location, now: Instant) {
        let server = &self.servers[&location];
        if !server.is_connected() && location == Location::Remote {
            return;
        }
        for (name, flow) in &mut self.flows {
            let Some(part) = flow.parts.get_mut(&location).filter(|part| !part.connected) else {
                continue;
            };
            // After lost events the server still has the part, so it is removed first. If the
            // server restarted, it has nothing to remove.
            if let Some(session) = part.session {
                let _ = server.remove_flow(session);
            }
            match install(server.as_ref(), part, &mut flow.router, now) {
                Ok(()) => info!(flow = name.as_str(), ?location, "installed flow"),
                // A server that refuses the part needs attention; one that cannot be reached is
                // tried again at every check, so it is not worth a warning each time.
                Err(e @ Error::InvalidArgument(_)) => {
                    warn!(flow = name.as_str(), ?location, error = %e, "server refused flow");
                }
                Err(e) => {
                    debug!(flow = name.as_str(), ?location, error = %e, "failed to install flow");
                }
            }
        }
    }

    /// Carries out what the routers decided: sends inputs on their crossings, and reports offers
    /// that ended on the client.
    fn carry_out(&mut self) {
        for (&name, flow) in &mut self.flows {
            while let Some(action) = flow.router.poll_action() {
                match action {
                    RouterAction::Send {
                        edge,
                        offer,
                        input_id,
                        input,
                        ..
                    } => {
                        let location = flow.crossings[&edge].location;
                        // A crossing only sends while its part is installed.
                        let Some(session) = flow.parts[&location].session else {
                            continue;
                        };
                        // An input the server did not get times out on its crossing, which frees
                        // the crossing and, later, the input's token.
                        warn_on_error(
                            self.servers[&location].send(
                                session,
                                offer,
                                input_id,
                                input.payload_type,
                                input.bytes,
                            ),
                            "failed to send an input to a server",
                        );
                    }
                    RouterAction::Skipped {
                        edge,
                        offer,
                        input_id,
                    } => {
                        let crossing = &flow.crossings[&edge];
                        for &engine in &crossing.targets {
                            let record = EventRecord::skipped(offer, input_id, engine, name);
                            publish(
                                &self.node,
                                &mut self.producers,
                                &mut self.relays,
                                record,
                                &[],
                            );
                        }
                        // The engines' subscribers on the server's machine learn it from the
                        // server. A notice that does not arrive only leaves them waiting.
                        let part = &flow.parts[&crossing.location];
                        if part.connected
                            && let Some(session) = part.session
                        {
                            warn_on_error(
                                self.servers[&crossing.location]
                                    .send_skipped(session, offer, input_id),
                                "failed to send a skip notice to a server",
                            );
                        }
                    }
                    RouterAction::TimedOut {
                        edge,
                        offer,
                        input_id,
                    } => {
                        for &engine in &flow.crossings[&edge].targets {
                            let record = EventRecord::timed_out(offer, input_id, engine, name);
                            publish(
                                &self.node,
                                &mut self.producers,
                                &mut self.relays,
                                record,
                                &[],
                            );
                        }
                    }
                }
            }
        }
    }
}

/// Publishes `record` for the app of its input's producer, and on its engine's service if the
/// engine is relayed. An event a subscriber misses only leaves it waiting, so the thread carries
/// on after a failure.
fn publish(
    node: &Node<ipc::Service>,
    producers: &mut Producers,
    relays: &mut Relays,
    record: EventRecord,
    result: &[u8],
) {
    warn_on_error(
        relays.publish(node, &record, result),
        "failed to publish an event for a relayed engine",
    );
    let producer = record.id.producer;
    warn_on_error(
        producers.publish(&producer, record, result),
        "failed to publish an event for a producer's app",
    );
}

/// Installs `part` on `server`, and lets its crossings in `router` send.
fn install(
    server: &dyn ServerLink,
    part: &mut Part,
    router: &mut FlowRouter<Data>,
    now: Instant,
) -> Result<()> {
    let session = server.install(part.install.clone())?;
    part.session = Some(session);
    part.connected = true;
    router.reconnect(&part.crossings, now);
    Ok(())
}

/// Logs the error of `result`, a failure the thread carries on after, as a warning about `what`.
fn warn_on_error(result: Result<()>, what: &str) {
    if let Err(e) = result {
        warn!(error = %e, "{what}");
    }
}
