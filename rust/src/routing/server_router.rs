//! Server-side routing: the crossings that arrive from clients, the edges between the server's own
//! engines, and the delivery of inputs to each engine. Like the client's router, it does no I/O and
//! reads no clock. The server calls in with what happened, and carries out the actions it queues.
//!
//! Delivery is pulled by the engines: an edge holds its newest input in its pending slot, where a
//! newer input replaces it, until its engine has room. The engine's edges then take turns. So an
//! engine always gets the newest input of each edge, however many edges feed it.
//!
//! The router is generic over `T`, the data of an input or a result as the server keeps it, e.g. a
//! reference-counted buffer and its payload type. It only stores, clones and hands back `T`.

use std::{
    collections::{HashMap, HashSet, VecDeque},
    time::{Duration, Instant},
};

use crate::{
    Error, Result,
    ipc::types::{EngineName, EventRecord, FlowName, Forward, MAX_CHILDREN, OutcomeRecord},
    routing::{
        edge::{EdgeAction, EdgeState},
        ids::{EdgeId, InputId, NodeId, OfferId},
    },
};

/// An edge whose offers this server makes: between two of its engines, or from a crossing to one
/// of its engines. It has no capacity of its own: the engine at its end pulls its inputs.
#[derive(Debug, Clone)]
pub(crate) struct EdgeSpec {
    pub(crate) id: EdgeId,
    /// The node that feeds the edge. For a crossing's target, it is on the client's side.
    pub(crate) from: NodeId,
    /// The engine at the end of the edge, on this server.
    pub(crate) to: NodeId,
    /// How long the engine may take to answer an input it was given.
    pub(crate) timeout: Duration,
}

/// A client's edge that arrives at this server. The client sends each input on it once; the
/// server offers it on one edge per target engine.
#[derive(Debug, Clone)]
pub(crate) struct CrossingSpec {
    /// The client's edge. Inputs sent on it carry an offer on this edge.
    pub(crate) id: EdgeId,
    pub(crate) targets: Vec<EdgeSpec>,
}

/// This server's part of a flow.
#[derive(Debug, Clone)]
pub(crate) struct InstallFlow {
    pub(crate) flow: String,
    /// This server's engines in the flow, by registered name.
    pub(crate) engines: Vec<(NodeId, String)>,
    /// Edges between this server's engines.
    pub(crate) edges: Vec<EdgeSpec>,
    /// Client edges arriving at this server.
    pub(crate) crossings: Vec<CrossingSpec>,
}

/// What the server must do after calling into the router.
#[derive(Debug)]
pub(crate) enum ServerAction<T> {
    /// Write `data`, the input `id`, into the input service of `engine`, as the offer `offer`.
    Deliver {
        engine: EngineName,
        offer: OfferId,
        id: InputId,
        data: T,
    },
    /// Publish `record` on the ordered event stream to the client that installed the flow
    /// `session`.
    ToClient {
        session: u64,
        record: EventRecord,
        result: Option<T>,
    },
    /// Publish `record` on the event service of the engine `record.engine`.
    ToSubscribers {
        record: EventRecord,
        result: Option<T>,
    },
}

/// An input kept on an edge, so the server can deliver it, and forward it after `Pass`.
#[derive(Debug, Clone)]
struct Kept<T> {
    id: InputId,
    data: T,
}

/// An edge this server owns.
#[derive(Debug)]
struct ServerEdge<T> {
    state: EdgeState<Kept<T>>,
    to: NodeId,
}

/// One installed flow.
#[derive(Debug)]
struct FlowState<T> {
    name: FlowName,
    nodes: HashMap<NodeId, EngineName>,
    edges: HashMap<EdgeId, ServerEdge<T>>,
    /// The server's edges leaving each engine.
    outgoing: HashMap<NodeId, Vec<EdgeId>>,
    /// The target edges of each crossing.
    crossings: HashMap<EdgeId, Vec<EdgeId>>,
}

/// Identifies an edge across flows: `(session, edge)`.
type EdgeKey = (u64, EdgeId);

/// Which edges an engine takes its next inputs from, and which inputs it holds.
#[derive(Debug, Default)]
struct EngineState {
    /// The edges with a pending input, in the order they get their next turn.
    turns: VecDeque<EdgeKey>,
    /// Inputs delivered to the engine whose outcome has not arrived. An input counts here even
    /// after its offer timed out, because it still takes a place in the engine's input buffer. It
    /// leaves when its outcome arrives, or when the server learns that no outcome will come.
    delivered: HashSet<OfferId>,
}

/// Routing of every flow installed on one server.
#[derive(Debug)]
pub(crate) struct ServerRouter<T> {
    next_session: u64,
    /// The most inputs delivered to one engine whose outcomes have not arrived.
    delivery_limit: usize,
    engines: HashMap<EngineName, EngineState>,
    /// Installed flows, by the session of their offer ids.
    flows: HashMap<u64, FlowState<T>>,
    actions: VecDeque<ServerAction<T>>,
}

impl<T: Clone> ServerRouter<T> {
    /// Creates a router whose first installed flow gets the session `first_session`. It delivers
    /// at most `delivery_limit` inputs to an engine before their outcomes arrive.
    pub(crate) fn new(first_session: u64, delivery_limit: usize) -> Self {
        assert!(delivery_limit > 0);
        Self {
            next_session: first_session,
            delivery_limit,
            engines: HashMap::new(),
            flows: HashMap::new(),
            actions: VecDeque::new(),
        }
    }

    /// Registers the engine `name`, so flows can use it.
    pub(crate) fn add_engine(&mut self, name: &str) -> Result<()> {
        let name = engine_name(name)?;
        if self.engines.contains_key(&name) {
            return Err(Error::InvalidArgument(format!(
                "engine {name:?} already exists"
            )));
        }
        self.engines.insert(name, EngineState::default());
        Ok(())
    }

    /// Unregisters the engine `name`. Fails while an installed flow uses it.
    pub(crate) fn remove_engine(&mut self, name: &str) -> Result<()> {
        let name = engine_name(name)?;
        if !self.engines.contains_key(&name) {
            return Err(Error::InvalidArgument(format!(
                "engine {name:?} does not exist"
            )));
        }
        let in_use = self
            .flows
            .values()
            .any(|flow| flow.nodes.values().any(|engine| *engine == name));
        if in_use {
            return Err(Error::InvalidArgument(format!(
                "engine {name:?} is used by an installed flow"
            )));
        }
        self.engines.remove(&name);
        Ok(())
    }

    /// Installs this server's part of a flow, and returns the session of its offer ids. The
    /// client names this session when it sends inputs to the flow.
    pub(crate) fn install(&mut self, spec: InstallFlow) -> Result<u64> {
        let flow = self.check(&spec)?;
        let session = self.next_session;
        self.next_session += 1;

        let mut state = FlowState {
            name: flow,
            nodes: HashMap::new(),
            edges: HashMap::new(),
            outgoing: HashMap::new(),
            crossings: HashMap::new(),
        };
        for (node, name) in &spec.engines {
            state
                .nodes
                .insert(*node, engine_name(name).expect("checked"));
        }
        let new_edge = |edge: &EdgeSpec| ServerEdge {
            state: EdgeState::pulled(edge.id, session, edge.timeout),
            to: edge.to,
        };
        for edge in &spec.edges {
            state.edges.insert(edge.id, new_edge(edge));
            state.outgoing.entry(edge.from).or_default().push(edge.id);
        }
        for crossing in &spec.crossings {
            for target in &crossing.targets {
                state.edges.insert(target.id, new_edge(target));
            }
            let targets = crossing.targets.iter().map(|target| target.id).collect();
            state.crossings.insert(crossing.id, targets);
        }
        self.flows.insert(session, state);
        Ok(session)
    }

    /// Checks `spec` against the engines registered here, and returns its flow name.
    fn check(&self, spec: &InstallFlow) -> Result<FlowName> {
        let flow = flow_name(&spec.flow)?;
        let mut nodes = HashSet::new();
        for (node, name) in &spec.engines {
            if !self.engines.contains_key(&engine_name(name)?) {
                return Err(Error::InvalidArgument(format!(
                    "engine {name:?} is not registered"
                )));
            }
            if !nodes.insert(*node) {
                return Err(Error::InvalidArgument(format!(
                    "node {node:?} appears twice"
                )));
            }
        }

        let mut ids = HashSet::new();
        let mut unique = |id: EdgeId| {
            if ids.insert(id) {
                Ok(())
            } else {
                Err(Error::InvalidArgument(format!("edge {id:?} appears twice")))
            }
        };
        let ends_here = |edge: &EdgeSpec| {
            if nodes.contains(&edge.to) {
                Ok(())
            } else {
                Err(Error::InvalidArgument(format!(
                    "edge {:?} ends at {:?}, which is not an engine of this server",
                    edge.id, edge.to
                )))
            }
        };

        let mut outgoing: HashMap<NodeId, usize> = HashMap::new();
        for edge in &spec.edges {
            unique(edge.id)?;
            ends_here(edge)?;
            if !nodes.contains(&edge.from) {
                return Err(Error::InvalidArgument(format!(
                    "edge {:?} starts at {:?}, which is not an engine of this server",
                    edge.id, edge.from
                )));
            }
            *outgoing.entry(edge.from).or_default() += 1;
        }
        if let Some((node, _)) = outgoing.iter().find(|(_, count)| **count > MAX_CHILDREN) {
            return Err(Error::InvalidArgument(format!(
                "node {node:?} has more than {MAX_CHILDREN} outgoing edges"
            )));
        }

        for crossing in &spec.crossings {
            unique(crossing.id)?;
            if crossing.targets.is_empty() || crossing.targets.len() > MAX_CHILDREN {
                return Err(Error::InvalidArgument(format!(
                    "crossing {:?} needs between 1 and {MAX_CHILDREN} targets",
                    crossing.id
                )));
            }
            for target in &crossing.targets {
                unique(target.id)?;
                ends_here(target)?;
            }
        }
        Ok(flow)
    }

    /// Removes the flow `session`. Its pending inputs are dropped. Inputs already delivered keep
    /// their places in their engines until their outcomes arrive.
    pub(crate) fn remove_flow(&mut self, session: u64) -> Result<()> {
        if self.flows.remove(&session).is_none() {
            return Err(Error::InvalidArgument(format!(
                "flow {session} is not installed"
            )));
        }
        for engine in self.engines.values_mut() {
            engine.turns.retain(|(s, _)| *s != session);
        }
        Ok(())
    }

    /// Returns the next action for the server to carry out.
    pub(crate) fn poll_action(&mut self) -> Option<ServerAction<T>> {
        self.actions.pop_front()
    }

    /// The client sent the input `id` on a crossing of the flow `session`, as its offer `offer`.
    /// Offers it on the crossing's target edges.
    pub(crate) fn accept(
        &mut self,
        session: u64,
        offer: OfferId,
        id: InputId,
        data: T,
        now: Instant,
    ) -> Result<()> {
        let Some(flow) = self.flows.get_mut(&session) else {
            return Err(Error::InvalidArgument(format!(
                "flow {session} is not installed"
            )));
        };
        let Some(targets) = flow.crossings.get(&offer.edge).cloned() else {
            return Err(Error::InvalidArgument(format!(
                "edge {:?} is not a crossing of flow {session}",
                offer.edge
            )));
        };

        let mut children = Vec::with_capacity(targets.len());
        for target in targets {
            let kept = Kept {
                id,
                data: data.clone(),
            };
            let child = offer_on(flow, &mut self.engines, session, target, kept, now);
            children.push((flow.nodes[&flow.edges[&target].to], child));
        }

        // The client learns about every child at once; each engine's subscribers only about
        // their own.
        let all: Vec<OfferId> = children.iter().map(|(_, child)| *child).collect();
        let record = EventRecord::accepted(offer, id, EngineName::new(), flow.name, &all)
            .expect("install bounds the targets of a crossing");
        self.actions.push_back(ServerAction::ToClient {
            session,
            record,
            result: None,
        });
        for (engine, child) in children {
            let record = EventRecord::accepted(offer, id, engine, flow.name, &[child])
                .expect("one child is within the maximum");
            self.actions.push_back(ServerAction::ToSubscribers {
                record,
                result: None,
            });
        }

        self.settle(now);
        Ok(())
    }

    /// The engine `engine` reported `outcome`. `output` is the new input of an `Emit`, and
    /// `result` the result bytes, if any.
    ///
    /// The outcome frees the engine's place, ends its offer, and forwards the input on the
    /// server's edges leaving the engine. An outcome for an offer that already ended, e.g. after
    /// a timeout, is still reported, but forwards nothing. An outcome for an input that was not
    /// delivered to this engine, or was already answered, is ignored. An `Emit` without an output
    /// is an error, and its input is lost, as in [`ServerRouter::input_lost`].
    pub(crate) fn outcome(
        &mut self,
        engine: &str,
        outcome: &OutcomeRecord,
        output: Option<T>,
        result: Option<T>,
        now: Instant,
    ) -> Result<()> {
        let engine = engine_name(engine)?;
        if outcome.forward == Forward::Emit && output.is_none() {
            // The engine answered, but there is nothing to forward: the input is lost.
            self.lose_input(&engine, outcome.offer, now)?;
            return Err(Error::InvalidArgument(String::from(
                "an emit needs an output",
            )));
        }
        let Some(engine_state) = self.engines.get_mut(&engine) else {
            return Err(Error::InvalidArgument(format!(
                "engine {engine:?} does not exist"
            )));
        };
        let offer = outcome.offer;
        if !engine_state.delivered.remove(&offer) {
            return Ok(());
        }

        // The flow may have been removed while the engine worked on the input.
        if let Some(flow) = self.flows.get_mut(&offer.session) {
            let edge = flow
                .edges
                .get_mut(&offer.edge)
                .expect("a delivered offer's edge exists while its flow does");
            let node = edge.to;
            let kept = edge.state.end(offer, now);

            let mut children = Vec::new();
            if let Some(kept) = kept {
                let forwarded = match outcome.forward {
                    Forward::Stop => None,
                    Forward::Pass => Some(kept.data),
                    Forward::Emit => output,
                };
                if let Some(data) = forwarded {
                    let next_edges = flow.outgoing.get(&node).cloned().unwrap_or_default();
                    for next in next_edges {
                        let kept = Kept {
                            id: outcome.id,
                            data: data.clone(),
                        };
                        children.push(offer_on(
                            flow,
                            &mut self.engines,
                            offer.session,
                            next,
                            kept,
                            now,
                        ));
                    }
                }
            }

            let record = EventRecord::result(
                offer,
                outcome.id,
                engine,
                flow.name,
                outcome.forward,
                &children,
                outcome.payload_type,
            )
            .expect("install bounds the outgoing edges of an engine");
            publish(&mut self.actions, offer.session, record, result);
        }
        self.settle(now);
        Ok(())
    }

    /// The input `offer`, delivered to the engine `engine`, will get no outcome: it did not reach
    /// the engine, or its outcome could not be read. Frees its place in the engine, and ends its
    /// offer with `TimedOut` if the offer has not ended yet. Does nothing for an input that was
    /// not delivered to this engine, or that was already answered.
    pub(crate) fn input_lost(&mut self, engine: &str, offer: OfferId, now: Instant) -> Result<()> {
        let engine = engine_name(engine)?;
        self.lose_input(&engine, offer, now)
    }

    fn lose_input(&mut self, engine: &EngineName, offer: OfferId, now: Instant) -> Result<()> {
        let Some(engine_state) = self.engines.get_mut(engine) else {
            return Err(Error::InvalidArgument(format!(
                "engine {engine:?} does not exist"
            )));
        };
        if engine_state.delivered.remove(&offer) {
            self.lose(offer, now);
            self.settle(now);
        }
        Ok(())
    }

    /// The engine `engine` stopped, e.g. its process died, so it will answer none of its inputs.
    /// The inputs delivered to it end with `TimedOut` and free their places, and the inputs
    /// pending for it end with `Skipped`. The engine stays registered and its flows stay
    /// installed, so an engine started again under the same name picks up new inputs.
    pub(crate) fn engine_lost(&mut self, engine: &str, now: Instant) -> Result<()> {
        let engine = engine_name(engine)?;
        let Some(engine_state) = self.engines.get_mut(&engine) else {
            return Err(Error::InvalidArgument(format!(
                "engine {engine:?} does not exist"
            )));
        };
        engine_state.turns.clear();
        let delivered: Vec<OfferId> = engine_state.delivered.drain().collect();
        for offer in delivered {
            self.lose(offer, now);
        }
        for flow in self.flows.values_mut() {
            for edge in flow.edges.values_mut() {
                if flow.nodes[&edge.to] == engine {
                    edge.state.skip_pending();
                }
            }
        }
        self.settle(now);
        Ok(())
    }

    /// Whether the engine `engine` holds inputs whose outcomes have not arrived.
    pub(crate) fn holds_inputs(&self, engine: &str) -> bool {
        engine_name(engine)
            .ok()
            .and_then(|engine| self.engines.get(&engine))
            .is_some_and(|state| !state.delivered.is_empty())
    }

    /// Ends the delivered offer `offer` with `TimedOut`, unless it already ended or its flow was
    /// removed.
    fn lose(&mut self, offer: OfferId, now: Instant) {
        if let Some(edge) = self
            .flows
            .get_mut(&offer.session)
            .and_then(|flow| flow.edges.get_mut(&offer.edge))
        {
            edge.state.lose(offer, now);
        }
    }

    /// Ends every delivered offer whose deadline passed. The server calls this periodically.
    /// Pending offers have no deadline: they wait until their engine has room, or a newer input
    /// replaces them.
    pub(crate) fn tick(&mut self, now: Instant) {
        for flow in self.flows.values_mut() {
            for edge in flow.edges.values_mut() {
                edge.state.expire(now);
            }
        }
        self.settle(now);
    }

    /// Reports what the edges did, delivers inputs to every engine with room, and reports those
    /// deliveries.
    fn settle(&mut self, now: Instant) {
        self.drain_edge_actions();
        self.deliver(now);
        self.drain_edge_actions();
    }

    /// Pulls pending inputs into every engine with room, taking the engine's edges in turn.
    fn deliver(&mut self, now: Instant) {
        for state in self.engines.values_mut() {
            while state.delivered.len() < self.delivery_limit {
                let Some((session, edge)) = state.turns.pop_front() else {
                    break;
                };
                let pulled = self
                    .flows
                    .get_mut(&session)
                    .and_then(|flow| flow.edges.get_mut(&edge))
                    .and_then(|edge| edge.state.pull(now));
                if let Some(offer) = pulled {
                    state.delivered.insert(offer);
                }
            }
        }
    }

    /// Turns the edges' queued actions into events, and sends become deliveries.
    fn drain_edge_actions(&mut self) {
        for (&session, flow) in &mut self.flows {
            for edge in flow.edges.values_mut() {
                let engine = flow.nodes[&edge.to];
                while let Some(action) = edge.state.poll_action() {
                    match action {
                        EdgeAction::Send { offer, input } => {
                            let record = EventRecord::sent(offer, input.id, engine, flow.name);
                            publish(&mut self.actions, session, record, None);
                            self.actions.push_back(ServerAction::Deliver {
                                engine,
                                offer,
                                id: input.id,
                                data: input.data,
                            });
                        }
                        EdgeAction::Skipped { offer, input } => {
                            let record = EventRecord::skipped(offer, input.id, engine, flow.name);
                            publish(&mut self.actions, session, record, None);
                        }
                        EdgeAction::TimedOut { offer, input } => {
                            let record = EventRecord::timed_out(offer, input.id, engine, flow.name);
                            publish(&mut self.actions, session, record, None);
                        }
                    }
                }
            }
        }
    }
}

/// Offers `kept` on the edge `edge` of `flow`, whose session is `session`. An edge that had no
/// pending input joins the turns of its engine.
fn offer_on<T: Clone>(
    flow: &mut FlowState<T>,
    engines: &mut HashMap<EngineName, EngineState>,
    session: u64,
    edge: EdgeId,
    kept: Kept<T>,
    now: Instant,
) -> OfferId {
    let state = flow.edges.get_mut(&edge).expect("the flow's edges exist");
    let joins_turns = !state.state.has_pending();
    let offer = state.state.offer(kept, now);
    if joins_turns {
        engines
            .get_mut(&flow.nodes[&state.to])
            .expect("installed flows use registered engines")
            .turns
            .push_back((session, edge));
    }
    offer
}

/// Queues `record` for the client of the flow `session` and for the engine's subscribers.
fn publish<T: Clone>(
    actions: &mut VecDeque<ServerAction<T>>,
    session: u64,
    record: EventRecord,
    result: Option<T>,
) {
    actions.push_back(ServerAction::ToClient {
        session,
        record: record.clone(),
        result: result.clone(),
    });
    actions.push_back(ServerAction::ToSubscribers { record, result });
}

fn engine_name(name: &str) -> Result<EngineName> {
    if name.is_empty() {
        return Err(Error::InvalidArgument(String::from("engine name is empty")));
    }
    EngineName::try_from(name)
        .map_err(|_| Error::InvalidArgument(format!("engine name {name:?} is too long")))
}

fn flow_name(name: &str) -> Result<FlowName> {
    if name.is_empty() {
        return Err(Error::InvalidArgument(String::from("flow name is empty")));
    }
    FlowName::try_from(name)
        .map_err(|_| Error::InvalidArgument(format!("flow name {name:?} is too long")))
}

#[cfg(test)]
mod tests {
    use iceoryx2_bb_container::vector::Vector;

    use super::*;
    use crate::{
        ipc::types::{EventKind, PayloadType},
        routing::ids::ProducerName,
    };

    const FIRST_SESSION: u64 = 100;
    const CLIENT_SESSION: u64 = 7;
    const ONE: usize = 1;
    const TWO: usize = 2;
    const TIMEOUT: Duration = Duration::from_secs(1);
    const FLOW: &str = "tracking";
    const DETECTOR: &str = "detector";
    const TRACKER: &str = "tracker";
    /// The client-side node that feeds the crossings.
    const SOURCE: NodeId = NodeId(0);
    const DETECTOR_NODE: NodeId = NodeId(1);
    const TRACKER_NODE: NodeId = NodeId(2);
    /// The crossings from the client to the detector, and their target edges.
    const CROSSING: EdgeId = EdgeId(10);
    const TO_DETECTOR: EdgeId = EdgeId(11);
    const OTHER_CROSSING: EdgeId = EdgeId(20);
    const OTHER_TO_DETECTOR: EdgeId = EdgeId(21);
    /// The server's edge from the detector to the tracker.
    const TO_TRACKER: EdgeId = EdgeId(30);
    const FRAME: u32 = 5;
    const OUTPUT: u32 = 9;

    /// What an action did, in a form tests can compare.
    #[derive(Debug, PartialEq)]
    enum Step {
        Deliver {
            engine: String,
            offer: OfferId,
            data: u32,
        },
        Client {
            kind: EventKind,
            children: Vec<OfferId>,
        },
        Subscribers {
            kind: EventKind,
            engine: String,
            children: Vec<OfferId>,
        },
    }

    fn steps(router: &mut ServerRouter<u32>) -> Vec<Step> {
        std::iter::from_fn(|| router.poll_action())
            .map(|action| match action {
                ServerAction::Deliver {
                    engine,
                    offer,
                    data,
                    ..
                } => Step::Deliver {
                    engine: engine.to_string(),
                    offer,
                    data,
                },
                ServerAction::ToClient { record, .. } => Step::Client {
                    kind: record.kind,
                    children: record.children.as_slice().to_vec(),
                },
                ServerAction::ToSubscribers { record, .. } => Step::Subscribers {
                    kind: record.kind,
                    engine: record.engine.to_string(),
                    children: record.children.as_slice().to_vec(),
                },
            })
            .collect()
    }

    /// The offers delivered to engines, in order.
    fn delivered(steps: &[Step]) -> Vec<(String, OfferId, u32)> {
        steps
            .iter()
            .filter_map(|step| match step {
                Step::Deliver {
                    engine,
                    offer,
                    data,
                } => Some((engine.clone(), *offer, *data)),
                _ => None,
            })
            .collect()
    }

    /// The kinds of the events sent to the client, in order.
    fn client_kinds(steps: &[Step]) -> Vec<EventKind> {
        steps
            .iter()
            .filter_map(|step| match step {
                Step::Client { kind, .. } => Some(*kind),
                _ => None,
            })
            .collect()
    }

    fn input_id(seq: u64) -> InputId {
        InputId {
            producer: ProducerName::try_from("camera").unwrap(),
            session: CLIENT_SESSION,
            seq,
        }
    }

    /// The client's offer of its `seq`th input on `crossing`.
    fn client_offer(crossing: EdgeId, seq: u64) -> OfferId {
        OfferId {
            session: CLIENT_SESSION,
            edge: crossing,
            seq,
        }
    }

    fn edge(id: EdgeId, from: NodeId, to: NodeId) -> EdgeSpec {
        EdgeSpec {
            id,
            from,
            to,
            timeout: TIMEOUT,
        }
    }

    fn crossing(id: EdgeId, target: EdgeId) -> CrossingSpec {
        CrossingSpec {
            id,
            targets: vec![edge(target, SOURCE, DETECTOR_NODE)],
        }
    }

    /// A flow where the client feeds the detector over a crossing, and the detector feeds the
    /// tracker.
    fn spec() -> InstallFlow {
        InstallFlow {
            flow: String::from(FLOW),
            engines: vec![
                (DETECTOR_NODE, String::from(DETECTOR)),
                (TRACKER_NODE, String::from(TRACKER)),
            ],
            edges: vec![edge(TO_TRACKER, DETECTOR_NODE, TRACKER_NODE)],
            crossings: vec![crossing(CROSSING, TO_DETECTOR)],
        }
    }

    fn router(delivery_limit: usize) -> ServerRouter<u32> {
        let mut router = ServerRouter::new(FIRST_SESSION, delivery_limit);
        router.add_engine(DETECTOR).unwrap();
        router.add_engine(TRACKER).unwrap();
        router
    }

    /// A router with `spec()` installed, and the flow's session.
    fn installed(delivery_limit: usize) -> (ServerRouter<u32>, u64) {
        let mut router = router(delivery_limit);
        let session = router.install(spec()).unwrap();
        (router, session)
    }

    /// Sends input `seq`, with `data`, on `crossing`, and returns what the router did.
    fn accept_data(
        router: &mut ServerRouter<u32>,
        session: u64,
        crossing: EdgeId,
        seq: u64,
        data: u32,
        now: Instant,
    ) -> Vec<Step> {
        router
            .accept(
                session,
                client_offer(crossing, seq),
                input_id(seq),
                data,
                now,
            )
            .unwrap();
        steps(router)
    }

    /// Sends input `seq` on `crossing`, and returns what the router did.
    fn accept(
        router: &mut ServerRouter<u32>,
        session: u64,
        crossing: EdgeId,
        seq: u64,
        now: Instant,
    ) -> Vec<Step> {
        accept_data(router, session, crossing, seq, FRAME, now)
    }

    fn only_delivery(steps: &[Step]) -> OfferId {
        let [(_, offer, _)] = delivered(steps)[..] else {
            panic!("expected one delivery, got {steps:?}");
        };
        offer
    }

    fn pass(offer: OfferId, seq: u64) -> OutcomeRecord {
        OutcomeRecord::pass(offer, input_id(seq), PayloadType::new(), 0)
    }

    fn stop(offer: OfferId, seq: u64) -> OutcomeRecord {
        OutcomeRecord::stop(offer, input_id(seq), PayloadType::new(), 0)
    }

    #[test]
    fn install_rejects_an_unregistered_engine() {
        let mut router = ServerRouter::<u32>::new(FIRST_SESSION, ONE);
        router.add_engine(DETECTOR).unwrap();

        let result = router.install(spec());

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn install_rejects_a_repeated_edge_id() {
        let mut router = router(ONE);
        let mut spec = spec();
        spec.edges[0].id = TO_DETECTOR;

        let result = router.install(spec);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn install_rejects_an_edge_to_an_engine_elsewhere() {
        let mut router = router(ONE);
        let mut spec = spec();
        spec.edges[0].to = SOURCE;

        let result = router.install(spec);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn each_install_gets_a_new_session() {
        let mut router = router(ONE);

        let first = router.install(spec()).unwrap();
        let second = router.install(spec()).unwrap();

        assert_eq!(first, FIRST_SESSION);
        assert_ne!(first, second);
    }

    #[test]
    fn engine_used_by_a_flow_cannot_be_removed() {
        let (mut router, session) = installed(ONE);

        assert!(router.remove_engine(DETECTOR).is_err());
        router.remove_flow(session).unwrap();
        assert!(router.remove_engine(DETECTOR).is_ok());
    }

    #[test]
    fn accepted_input_is_reported_then_delivered() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);

        let steps = accept(&mut router, session, CROSSING, 1, now);

        let child = only_delivery(&steps);
        assert_eq!(child.session, session);
        assert_eq!(child.edge, TO_DETECTOR);
        assert_eq!(
            steps,
            vec![
                Step::Client {
                    kind: EventKind::Accepted,
                    children: vec![child],
                },
                Step::Subscribers {
                    kind: EventKind::Accepted,
                    engine: String::from(DETECTOR),
                    children: vec![child],
                },
                Step::Client {
                    kind: EventKind::Sent,
                    children: vec![],
                },
                Step::Subscribers {
                    kind: EventKind::Sent,
                    engine: String::from(DETECTOR),
                    children: vec![],
                },
                Step::Deliver {
                    engine: String::from(DETECTOR),
                    offer: child,
                    data: FRAME,
                },
            ]
        );
    }

    #[test]
    fn unknown_crossing_is_rejected() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);

        let result = router.accept(
            session,
            client_offer(TO_TRACKER, 1),
            input_id(1),
            FRAME,
            now,
        );

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn pass_forwards_on_the_server_edge_after_reporting_the_result() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));

        router
            .outcome(DETECTOR, &pass(offer, 1), None, None, now)
            .unwrap();
        let steps = steps(&mut router);

        let deliveries = delivered(&steps);
        let [(engine, child, _)] = &deliveries[..] else {
            panic!("expected one delivery, got {steps:?}");
        };
        assert_eq!(engine, TRACKER);
        assert_eq!(child.edge, TO_TRACKER);
        assert_eq!(
            steps[0],
            Step::Client {
                kind: EventKind::Result,
                children: vec![*child],
            }
        );
        assert_eq!(
            client_kinds(&steps),
            vec![EventKind::Result, EventKind::Sent]
        );
    }

    #[test]
    fn emit_forwards_the_new_output() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        let emit = OutcomeRecord::emit(
            offer,
            input_id(1),
            PayloadType::new(),
            PayloadType::new(),
            1,
            0,
        )
        .unwrap();

        router
            .outcome(DETECTOR, &emit, Some(OUTPUT), None, now)
            .unwrap();
        let steps = steps(&mut router);

        assert_eq!(
            delivered(&steps)
                .into_iter()
                .map(|(engine, _, data)| (engine, data))
                .collect::<Vec<_>>(),
            vec![(String::from(TRACKER), OUTPUT)]
        );
    }

    #[test]
    fn emit_without_an_output_is_rejected() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        let emit = OutcomeRecord::emit(
            offer,
            input_id(1),
            PayloadType::new(),
            PayloadType::new(),
            1,
            0,
        )
        .unwrap();

        let result = router.outcome(DETECTOR, &emit, None, None, now);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn stop_forwards_nothing() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));

        router
            .outcome(DETECTOR, &stop(offer, 1), None, None, now)
            .unwrap();
        let steps = steps(&mut router);

        assert_eq!(client_kinds(&steps), vec![EventKind::Result]);
        assert!(delivered(&steps).is_empty());
    }

    #[test]
    fn busy_engine_gets_the_newest_input_of_an_edge() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let first = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        accept_data(&mut router, session, CROSSING, 2, FRAME, now);

        let replaced = accept_data(&mut router, session, CROSSING, 3, OUTPUT, now);
        assert_eq!(
            client_kinds(&replaced),
            vec![EventKind::Accepted, EventKind::Skipped]
        );
        assert!(delivered(&replaced).is_empty());

        router
            .outcome(DETECTOR, &stop(first, 1), None, None, now)
            .unwrap();
        let [(_, _, data)] = delivered(&steps(&mut router))[..] else {
            panic!("expected one delivery");
        };
        assert_eq!(data, OUTPUT);
    }

    #[test]
    fn engine_holds_up_to_the_delivery_limit() {
        let now = Instant::now();
        let mut router = router(TWO);
        let mut spec = spec();
        spec.crossings
            .push(crossing(OTHER_CROSSING, OTHER_TO_DETECTOR));
        let session = router.install(spec).unwrap();

        accept(&mut router, session, CROSSING, 1, now);
        accept(&mut router, session, OTHER_CROSSING, 2, now);
        let third = accept(&mut router, session, CROSSING, 3, now);

        assert_eq!(
            router.engines[&engine_name(DETECTOR).unwrap()]
                .delivered
                .len(),
            TWO
        );
        assert!(delivered(&third).is_empty());
    }

    #[test]
    fn late_outcome_is_reported_but_forwards_nothing() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        router.tick(now + TIMEOUT);
        assert_eq!(client_kinds(&steps(&mut router)), vec![EventKind::TimedOut]);

        router
            .outcome(DETECTOR, &pass(offer, 1), None, None, now + TIMEOUT)
            .unwrap();
        let steps = steps(&mut router);

        assert_eq!(
            steps[0],
            Step::Client {
                kind: EventKind::Result,
                children: vec![],
            }
        );
        assert!(delivered(&steps).is_empty());
    }

    #[test]
    fn duplicate_outcome_is_ignored() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        router
            .outcome(DETECTOR, &stop(offer, 1), None, None, now)
            .unwrap();
        steps(&mut router);

        router
            .outcome(DETECTOR, &stop(offer, 1), None, None, now)
            .unwrap();

        assert!(steps(&mut router).is_empty());
    }

    #[test]
    fn outcome_for_another_engines_input_is_ignored() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));

        router
            .outcome(TRACKER, &stop(offer, 1), None, None, now)
            .unwrap();
        assert!(steps(&mut router).is_empty());

        router
            .outcome(DETECTOR, &stop(offer, 1), None, None, now)
            .unwrap();
        assert_eq!(client_kinds(&steps(&mut router)), vec![EventKind::Result]);
    }

    #[test]
    fn pending_input_waits_while_the_engine_is_full() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let first = only_delivery(&accept(&mut router, session, CROSSING, 1, now));

        let waiting = accept(&mut router, session, CROSSING, 2, now);
        assert!(delivered(&waiting).is_empty());

        router
            .outcome(DETECTOR, &stop(first, 1), None, None, now)
            .unwrap();
        let second = only_delivery(&steps(&mut router));
        assert_eq!(second.seq, first.seq + 1);
    }

    #[test]
    fn edges_take_turns_at_a_busy_engine() {
        let now = Instant::now();
        let mut router = router(ONE);
        let mut spec = spec();
        spec.crossings
            .push(crossing(OTHER_CROSSING, OTHER_TO_DETECTOR));
        let session = router.install(spec).unwrap();
        let first = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        accept(&mut router, session, CROSSING, 2, now);
        accept(&mut router, session, OTHER_CROSSING, 3, now);

        router
            .outcome(DETECTOR, &stop(first, 1), None, None, now)
            .unwrap();
        let second = only_delivery(&steps(&mut router));
        // The first edge is pending again before the other edge got its turn.
        accept(&mut router, session, CROSSING, 4, now);
        router
            .outcome(DETECTOR, &stop(second, 2), None, None, now)
            .unwrap();
        let third = only_delivery(&steps(&mut router));

        assert_eq!(second.edge, TO_DETECTOR);
        assert_eq!(third.edge, OTHER_TO_DETECTOR);
    }

    #[test]
    fn pending_input_does_not_time_out() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        accept(&mut router, session, CROSSING, 1, now);
        accept(&mut router, session, CROSSING, 2, now);

        router.tick(now + TIMEOUT);

        // Only the delivered input times out; the pending one keeps waiting.
        assert_eq!(client_kinds(&steps(&mut router)), vec![EventKind::TimedOut]);
    }

    #[test]
    fn timed_out_input_keeps_its_place_until_its_outcome() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let first = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        router.tick(now + TIMEOUT);
        steps(&mut router);

        let waiting = accept(&mut router, session, CROSSING, 2, now + TIMEOUT);
        assert!(delivered(&waiting).is_empty());

        router
            .outcome(DETECTOR, &stop(first, 1), None, None, now + TIMEOUT)
            .unwrap();
        let second = only_delivery(&steps(&mut router));
        assert_eq!(second.seq, first.seq + 1);
    }

    #[test]
    fn lost_input_times_out_and_frees_its_place() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let first = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        accept(&mut router, session, CROSSING, 2, now);

        router.input_lost(DETECTOR, first, now).unwrap();
        let steps = steps(&mut router);

        assert_eq!(
            client_kinds(&steps),
            vec![EventKind::TimedOut, EventKind::Sent]
        );
        assert_eq!(only_delivery(&steps).seq, first.seq + 1);
    }

    #[test]
    fn outcome_after_the_input_was_lost_is_ignored() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        router.input_lost(DETECTOR, offer, now).unwrap();
        steps(&mut router);

        router
            .outcome(DETECTOR, &stop(offer, 1), None, None, now)
            .unwrap();

        assert!(steps(&mut router).is_empty());
    }

    #[test]
    fn input_lost_at_another_engine_keeps_its_place() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));

        router.input_lost(TRACKER, offer, now).unwrap();

        assert!(steps(&mut router).is_empty());
        let waiting = accept(&mut router, session, CROSSING, 2, now);
        assert!(delivered(&waiting).is_empty());
    }

    #[test]
    fn emit_without_an_output_loses_the_input() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        let emit = OutcomeRecord::emit(
            offer,
            input_id(1),
            PayloadType::new(),
            PayloadType::new(),
            OUTPUT,
            0,
        )
        .unwrap();

        let result = router.outcome(DETECTOR, &emit, None, None, now);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
        assert_eq!(client_kinds(&steps(&mut router)), vec![EventKind::TimedOut]);
        let next = accept(&mut router, session, CROSSING, 2, now);
        assert_eq!(delivered(&next).len(), ONE);
    }

    #[test]
    fn lost_engine_ends_its_delivered_and_pending_inputs() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let first = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        accept(&mut router, session, CROSSING, 2, now);

        router.engine_lost(DETECTOR, now).unwrap();

        assert_eq!(
            client_kinds(&steps(&mut router)),
            vec![EventKind::TimedOut, EventKind::Skipped]
        );
        // An outcome the dead engine sent before it died changes nothing.
        router
            .outcome(DETECTOR, &stop(first, 1), None, None, now)
            .unwrap();
        assert!(steps(&mut router).is_empty());
    }

    #[test]
    fn engine_holds_inputs_from_delivery_to_outcome() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        assert!(!router.holds_inputs(DETECTOR));

        let offer = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        assert!(router.holds_inputs(DETECTOR));

        router
            .outcome(DETECTOR, &stop(offer, 1), None, None, now)
            .unwrap();
        assert!(!router.holds_inputs(DETECTOR));
    }

    #[test]
    fn engine_started_again_gets_new_inputs() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        accept(&mut router, session, CROSSING, 1, now);
        router.engine_lost(DETECTOR, now).unwrap();
        steps(&mut router);

        let next = accept(&mut router, session, CROSSING, 2, now);

        assert_eq!(delivered(&next).len(), ONE);
    }

    #[test]
    fn removing_a_flow_drops_its_pending_inputs_but_frees_places_on_outcome() {
        let now = Instant::now();
        let (mut router, session) = installed(ONE);
        let first = only_delivery(&accept(&mut router, session, CROSSING, 1, now));
        accept(&mut router, session, CROSSING, 2, now);
        router.remove_flow(session).unwrap();
        let other = router.install(spec()).unwrap();
        let waiting = accept(&mut router, other, CROSSING, 3, now);
        assert!(delivered(&waiting).is_empty());

        router
            .outcome(DETECTOR, &stop(first, 1), None, None, now)
            .unwrap();
        let next = only_delivery(&steps(&mut router));

        assert_eq!(next.session, other);
    }
}
