//! The server's work on one machine: it carries out the server router's actions on the engines'
//! iceoryx2 services, and feeds the router the engines' outcomes. Only [`Dispatcher::wait`]
//! blocks; the server thread waits there, then calls [`Dispatcher::poll`].
//!
//! Inputs from clients and events for them pass through plain calls here. The transports that
//! carry them, iceoryx2 to local clients ([`LocalClients`](super::local_clients::LocalClients))
//! and QUIC to remote clients, sit on top.

use std::{
    collections::{HashMap, VecDeque},
    sync::Arc,
    time::{Duration, Instant},
};

use iceoryx2::identifiers::UniqueNodeId;
use iceoryx2::node::NodeView;
use iceoryx2::port::{
    listener::Listener, notifier::Notifier, publisher::Publisher, subscriber::Subscriber,
};
use iceoryx2::prelude::*;
use iceoryx2_bb_container::string::String as _;
use tracing::{info, warn};
use uuid::Uuid;

use crate::{
    Error, Result,
    events::EventPublisher,
    ipc::types::{
        ClientEventHeader, Data, DerivedRecord, EngineName, EventRecord, InputHeader,
        OutcomeRecord, PayloadType,
    },
    naming,
    routing::{
        ids::{InputId, OfferId},
        server_router::{InstallFlow, ServerAction, ServerRouter},
    },
    services,
};

/// The slice length an engine's input publisher starts with. It grows as needed.
const INITIAL_SLICE_LEN: usize = 4096;

/// An event for the client that installed the flow `session`, in the order the client must see
/// it.
#[derive(Debug)]
pub(crate) struct ClientEvent {
    pub(crate) session: u64,
    pub(crate) record: EventRecord,
    pub(crate) result: Option<Data>,
    /// The input the engine forwarded, when the client needs it (`ServerAction::ToClient`).
    pub(crate) forwarded: Option<Data>,
}

impl ClientEvent {
    /// The header to send the event with, as event number `seq`.
    pub(crate) fn header(&self, seq: u64) -> ClientEventHeader {
        ClientEventHeader::new(self.record.clone().numbered(seq), self.forwarded.as_ref())
    }

    /// The bytes to send after the header: the forwarded input, then the result.
    pub(crate) fn payload(&self) -> [&[u8]; 2] {
        [
            bytes_of(self.forwarded.as_ref()),
            bytes_of(self.result.as_ref()),
        ]
    }
}

/// The bytes of `data`, or none.
fn bytes_of(data: Option<&Data>) -> &[u8] {
    data.map_or(&[], |data| &data.bytes)
}

/// The server's ends of one engine's services.
struct EnginePorts {
    inputs: Publisher<ipc::Service, [u8], InputHeader>,
    /// Wakes the engine after each delivered input.
    input_ready: Notifier<ipc::Service>,
    outcomes: Subscriber<ipc::Service, [u8], OutcomeRecord>,
    /// Wakes the server after each outcome and derived result.
    outcome_ready: Listener<ipc::Service>,
    derived: Subscriber<ipc::Service, [u8], DerivedRecord>,
    events: EventPublisher,
    /// The node of the engine's running process, as of the last check. A different node, or
    /// none, means that process stopped.
    instance: Option<UniqueNodeId>,
}

impl EnginePorts {
    fn open(node: &Node<ipc::Service>, name: &str) -> Result<Self> {
        let inputs = services::open_engine_input(node, name)?
            .publisher_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .allocation_strategy(AllocationStrategy::PowerOfTwo)
            .set_backpressure_handler(services::fail_when_full)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create input publisher: {e}")))?;
        let input_ready = services::open_engine_input_ready(node, name)?
            .notifier_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create input notifier: {e}")))?;
        let outcomes = services::open_engine_outcome(node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create outcome subscriber: {e}")))?;
        let outcome_ready = services::open_engine_outcome_ready(node, name)?
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create outcome listener: {e}")))?;
        let derived = services::open_engine_derived(node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create derived subscriber: {e}")))?;
        let events = EventPublisher::engine(node, name)?;
        Ok(Self {
            inputs,
            input_ready,
            outcomes,
            outcome_ready,
            derived,
            events,
            instance: None,
        })
    }

    /// Writes the input into the engine's input service. Fails if no engine received it, e.g.
    /// because the engine has not started yet, or if its buffer is full.
    fn deliver(&self, offer: OfferId, id: InputId, data: &Data) -> Result<()> {
        let mut sample = self
            .inputs
            .loan_slice_uninit(data.bytes.len())
            .map_err(|e| Error::Internal(format!("failed to loan input: {e}")))?;
        *sample.user_header_mut() = InputHeader {
            offer,
            id,
            payload_type: data.payload_type,
        };
        let receivers = sample
            .write_from_slice(&data.bytes)
            .send()
            .map_err(|e| Error::Internal(format!("failed to deliver input: {e}")))?;
        if receivers == 0 {
            return Err(Error::Internal(String::from(
                "no engine received the input",
            )));
        }
        Ok(())
    }

    /// Wakes the engine for an input just delivered.
    fn wake(&self) -> Result<()> {
        self.input_ready
            .notify()
            .map_err(|e| Error::Internal(format!("failed to wake the engine: {e}")))?;
        Ok(())
    }

    /// Numbers `record` and publishes it, with `result` as the payload.
    fn publish(&mut self, record: EventRecord, result: Option<&Data>) -> Result<()> {
        self.events.publish(record, bytes_of(result))
    }
}

/// Connects the server router to the engines on this machine.
pub(crate) struct Dispatcher {
    node: Node<ipc::Service>,
    router: ServerRouter<Data>,
    engines: HashMap<EngineName, EnginePorts>,
    /// Events for clients, until their transports send them.
    client_events: VecDeque<ClientEvent>,
}

impl Dispatcher {
    /// Creates the dispatcher of the server `name`.
    pub(crate) fn new(name: &str) -> Result<Self> {
        let node = NodeBuilder::new()
            .name(&naming::server_node(name)?)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        // A random first session keeps offer ids from before a restart apart from new ones.
        let (first_session, _) = Uuid::new_v4().as_u64_pair();
        Ok(Self {
            node,
            router: ServerRouter::new(first_session, services::ENGINE_DELIVERY_LIMIT),
            engines: HashMap::new(),
            client_events: VecDeque::new(),
        })
    }

    /// Starts serving the engine `name`.
    pub(crate) fn add_engine(&mut self, name: &str) -> Result<()> {
        let engine = EngineName::try_from(name)
            .map_err(|_| Error::InvalidArgument(format!("engine name {name:?} is too long")))?;
        if self.engines.contains_key(&engine) {
            return Err(Error::InvalidArgument(format!(
                "engine {name:?} already exists"
            )));
        }
        let ports = EnginePorts::open(&self.node, name)?;
        self.router.add_engine(name)?;
        self.engines.insert(engine, ports);
        info!(engine = name, "serving engine");
        Ok(())
    }

    /// Stops serving the engine `name`. Fails while an installed flow uses it.
    pub(crate) fn remove_engine(&mut self, name: &str) -> Result<()> {
        self.router.remove_engine(name)?;
        let engine = EngineName::try_from(name).expect("the router knew the engine");
        self.engines.remove(&engine);
        info!(engine = name, "stopped serving engine");
        Ok(())
    }

    /// Installs a client's part of a flow, and returns the flow's session.
    pub(crate) fn install(&mut self, spec: InstallFlow) -> Result<u64> {
        self.router.install(spec)
    }

    pub(crate) fn remove_flow(&mut self, session: u64) -> Result<()> {
        self.router.remove_flow(session)
    }

    /// A client sent the input `id` on a crossing of the flow `session`, as its offer `offer`.
    /// The bytes are copied once, and shared by every edge the input is offered on.
    pub(crate) fn accept(
        &mut self,
        session: u64,
        offer: OfferId,
        id: InputId,
        payload_type: PayloadType,
        bytes: &[u8],
        now: Instant,
    ) -> Result<()> {
        let data = Data {
            bytes: Arc::from(bytes),
            payload_type,
        };
        self.router.accept(session, offer, id, data, now)?;
        self.carry_out(now)
    }

    /// A client skipped its offer `offer` of the input `id` on a crossing of the flow `session`.
    /// The subscribers of the crossing's target engines are told.
    pub(crate) fn crossing_skipped(
        &mut self,
        session: u64,
        offer: OfferId,
        id: InputId,
        now: Instant,
    ) -> Result<()> {
        self.router.crossing_skipped(session, offer, id)?;
        self.carry_out(now)
    }

    /// The engine `name` stopped, e.g. its process died. Its delivered inputs end with
    /// `TimedOut` and its pending inputs with `Skipped`. It keeps being served, so it gets new
    /// inputs when it starts again.
    pub(crate) fn engine_lost(&mut self, name: &str, now: Instant) -> Result<()> {
        self.router.engine_lost(name, now)?;
        self.carry_out(now)
    }

    /// The iceoryx2 node of the server, for the services of its clients.
    pub(crate) fn node(&self) -> &Node<ipc::Service> {
        &self.node
    }

    /// Waits until an engine reports an outcome or a derived result, one of `others` is notified,
    /// or `timeout` passes. The outcomes themselves are read by [`Dispatcher::poll`], and the notifications of
    /// `others` by their owners.
    pub(crate) fn wait<'a>(
        &'a self,
        others: impl IntoIterator<Item = &'a Listener<ipc::Service>>,
        timeout: Duration,
    ) -> Result<()> {
        let outcome_ready = self.engines.values().map(|ports| &ports.outcome_ready);
        services::wait_for_any(outcome_ready.chain(others), timeout)?;
        // Consume the notifications, or the next wait returns at once for the same outcomes.
        for (engine, ports) in &self.engines {
            ports.outcome_ready.try_wait(|_| {}).map_err(|e| {
                Error::Internal(format!("failed to read wake-ups of engine {engine:?}: {e}"))
            })?;
        }
        Ok(())
    }

    /// Finds engines whose process stopped since the last check, and ends their inputs, as
    /// [`Dispatcher::engine_lost`] does. An engine's process is known by the iceoryx2 node it
    /// runs, so an engine that stopped and started again between two checks is noticed too.
    /// Also removes what the dead processes of engines left behind, so that inputs for an engine
    /// that is not running reach no one and are lost at once.
    pub(crate) fn check_engines(&mut self, now: Instant) -> Result<()> {
        let served = |node: &dyn NodeView| {
            let details = node.details().as_ref()?;
            let engine = naming::engine_of_node(details.name().as_str())?;
            let engine = EngineName::try_from(engine).ok()?;
            self.engines.contains_key(&engine).then_some(engine)
        };
        let mut running = HashMap::new();
        let mut first_error = None;
        Node::<ipc::Service>::list(self.node.config(), |state| {
            match state {
                NodeState::Alive(node) => {
                    if let Some(engine) = served(&node) {
                        running.entry(engine).or_insert(*node.id());
                    }
                }
                NodeState::Dead(node) => {
                    if served(&node).is_some()
                        && let Err(e) = node.try_remove_stale_resources()
                    {
                        first_error.get_or_insert(Error::Internal(format!(
                            "failed to clean up after a dead engine: {e:?}"
                        )));
                    }
                }
                NodeState::Inaccessible(_) | NodeState::Undefined(_) => {}
            }
            CallbackProgression::Continue
        })
        .map_err(|e| Error::Internal(format!("failed to list nodes: {e:?}")))?;

        // An engine stopped if the process seen at the last check is gone, or if no process runs
        // but inputs were delivered to one: it started and stopped between two checks.
        let mut stopped = Vec::new();
        for (engine, ports) in &mut self.engines {
            let instance = running.get(engine).copied();
            let replaced = ports.instance.is_some() && ports.instance != instance;
            let gone = instance.is_none() && self.router.holds_inputs(engine.as_str());
            if replaced || gone {
                stopped.push(*engine);
            }
            ports.instance = instance;
        }
        for engine in stopped {
            warn!(
                engine = engine.as_str(),
                "engine stopped; its inputs are lost"
            );
            if let Err(e) = self.engine_lost(engine.as_str(), now) {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    /// Handles what is ready: the engines' outcomes, and offers past their deadline. Returns the
    /// first error, after handling everything else.
    pub(crate) fn poll(&mut self, now: Instant) -> Result<()> {
        let mut first_error = None;
        for (engine, ports) in &self.engines {
            loop {
                let sample = match ports.outcomes.receive() {
                    Ok(Some(sample)) => sample,
                    Ok(None) => break,
                    Err(e) => {
                        first_error.get_or_insert(Error::Internal(format!(
                            "failed to receive outcome of {engine:?}: {e}"
                        )));
                        break;
                    }
                };
                let outcome = sample.user_header();
                let Some((output, result)) = split_outcome(outcome, sample.payload()) else {
                    // The outcome cannot be acted on, so no outcome will come for its input.
                    first_error.get_or_insert(Error::Internal(format!(
                        "outcome of {engine:?} is shorter than its lengths say"
                    )));
                    if let Err(e) = self.router.input_lost(engine.as_str(), outcome.offer, now) {
                        first_error.get_or_insert(e);
                    }
                    continue;
                };
                if let Err(e) = self
                    .router
                    .outcome(engine.as_str(), outcome, output, result, now)
                {
                    first_error.get_or_insert(e);
                }
            }
            loop {
                let sample = match ports.derived.receive() {
                    Ok(Some(sample)) => sample,
                    Ok(None) => break,
                    Err(e) => {
                        first_error.get_or_insert(Error::Internal(format!(
                            "failed to receive derived result of {engine:?}: {e}"
                        )));
                        break;
                    }
                };
                let derived = sample.user_header();
                let result = (!sample.payload().is_empty()).then(|| Data {
                    bytes: Arc::from(sample.payload()),
                    payload_type: derived.payload_type,
                });
                if let Err(e) = self.router.derived(engine.as_str(), derived, result) {
                    first_error.get_or_insert(e);
                }
            }
        }
        self.router.tick(now);
        if let Err(e) = self.carry_out(now) {
            first_error.get_or_insert(e);
        }
        first_error.map_or(Ok(()), Err)
    }

    /// Returns the next event for a client.
    pub(crate) fn poll_client_event(&mut self) -> Option<ClientEvent> {
        self.client_events.pop_front()
    }

    /// Carries out the router's actions. Returns the first error, after carrying out the rest.
    fn carry_out(&mut self, now: Instant) -> Result<()> {
        let mut first_error = None;
        while let Some(action) = self.router.poll_action() {
            let done = match action {
                ServerAction::Deliver {
                    engine,
                    offer,
                    id,
                    data,
                } => {
                    let delivered = ports(&mut self.engines, &engine)
                        .and_then(|ports| ports.deliver(offer, id, &data).map(|()| ports));
                    match delivered {
                        // An input whose wake-up failed is still in the engine's buffer, so it is
                        // not lost: the engine finds it when it polls.
                        Ok(ports) => ports.wake(),
                        // No outcome will come for an input that did not reach the engine. Losing
                        // it queues more actions, which this loop carries out too.
                        Err(e) => {
                            first_error
                                .get_or_insert(Error::Internal(format!("engine {engine:?}: {e}")));
                            self.router.input_lost(engine.as_str(), offer, now)
                        }
                    }
                }
                ServerAction::ToClient {
                    session,
                    record,
                    result,
                    forwarded,
                } => {
                    self.client_events.push_back(ClientEvent {
                        session,
                        record,
                        result,
                        forwarded,
                    });
                    Ok(())
                }
                ServerAction::ToSubscribers { record, result } => {
                    ports(&mut self.engines, &record.engine)
                        .and_then(|ports| ports.publish(record, result.as_ref()))
                }
            };
            if let Err(e) = done {
                first_error.get_or_insert(e);
            }
        }
        // A subscriber that is not woken still finds the events at its next wait.
        for ports in self.engines.values_mut() {
            if let Err(e) = ports.events.wake_subscribers() {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }
}

fn ports<'a>(
    engines: &'a mut HashMap<EngineName, EnginePorts>,
    engine: &EngineName,
) -> Result<&'a mut EnginePorts> {
    engines
        .get_mut(engine)
        .ok_or_else(|| Error::Internal(format!("engine {engine:?} has no services")))
}

/// Copies the new output, if any, and the result, if any, out of an outcome's payload. `None` if
/// the payload is shorter than the outcome's lengths say.
fn split_outcome(outcome: &OutcomeRecord, payload: &[u8]) -> Option<(Option<Data>, Option<Data>)> {
    let output_len = outcome.output_len as usize;
    let result_end = output_len.checked_add(outcome.result_len as usize)?;
    let (output, result) = payload.get(..result_end)?.split_at(output_len);
    let data = |bytes: &[u8], payload_type| {
        (!bytes.is_empty()).then(|| Data {
            bytes: Arc::from(bytes),
            payload_type,
        })
    };
    Some((
        data(output, outcome.output_type),
        data(result, outcome.payload_type),
    ))
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::{
        Engine, Subscription,
        ipc::types::{EventKind, Forward, payload_type},
        routing::{
            ids::{EdgeId, NodeId, ProducerName},
            server_router::{CrossingSpec, EdgeSpec},
        },
        subscription::Event,
        test_util::unique,
    };

    const CLIENT_SESSION: u64 = 7;
    const MAX_OUTPUT_SIZE: usize = 16;
    const TIMEOUT: Duration = Duration::from_secs(1);
    /// Far longer than a wake-up within one process takes.
    const LONG_WAIT: Duration = Duration::from_secs(5);
    const FRAME: [u8; 4] = [1, 2, 3, 4];
    const RESULT: [u8; 2] = [9, 8];
    const FRAME_TYPE: &str = "test.v1.Frame";
    const RESULT_TYPE: &str = "test.v1.Score";
    const SOURCE: NodeId = NodeId(0);
    const DETECTOR: NodeId = NodeId(1);
    const CROSSING: EdgeId = EdgeId(10);
    const TO_DETECTOR: EdgeId = EdgeId(11);

    /// A dispatcher serving one engine, the engine, and the flow that feeds it over a crossing.
    struct Fixture {
        dispatcher: Dispatcher,
        engine: Engine,
        name: String,
        session: u64,
    }

    impl Fixture {
        fn new() -> Self {
            let name = unique("detector");
            let mut dispatcher = Dispatcher::new(&unique("server")).unwrap();
            dispatcher.add_engine(&name).unwrap();
            let engine = Engine::new(&name, Some(RESULT_TYPE), MAX_OUTPUT_SIZE).unwrap();
            let session = dispatcher
                .install(InstallFlow {
                    flow: String::from("tracking"),
                    engines: vec![(DETECTOR, name.clone())],
                    edges: vec![],
                    crossings: vec![CrossingSpec {
                        id: CROSSING,
                        targets: vec![EdgeSpec {
                            id: TO_DETECTOR,
                            from: SOURCE,
                            to: DETECTOR,
                            timeout: TIMEOUT,
                        }],
                    }],
                    client_sources: vec![],
                })
                .unwrap();
            Self {
                dispatcher,
                engine,
                name,
                session,
            }
        }

        fn accept_frame(&mut self, now: Instant) {
            send_frame(&mut self.dispatcher, self.session, 1, now).unwrap();
        }

        fn client_kinds(&mut self) -> Vec<EventKind> {
            client_kinds(&mut self.dispatcher)
        }
    }

    /// The client sends its `seq`th frame on the crossing of the flow `session`.
    fn send_frame(dispatcher: &mut Dispatcher, session: u64, seq: u64, now: Instant) -> Result<()> {
        dispatcher.accept(
            session,
            OfferId {
                session: CLIENT_SESSION,
                edge: CROSSING,
                seq,
            },
            InputId {
                producer: ProducerName::try_from("camera").unwrap(),
                session: CLIENT_SESSION,
                seq,
            },
            payload_type(FRAME_TYPE).unwrap(),
            &FRAME,
            now,
        )
    }

    fn client_kinds(dispatcher: &mut Dispatcher) -> Vec<EventKind> {
        std::iter::from_fn(|| dispatcher.poll_client_event())
            .map(|event| event.record.kind)
            .collect()
    }

    #[test]
    fn accepted_input_reaches_the_engine() {
        let now = Instant::now();
        let mut fixture = Fixture::new();

        fixture.accept_frame(now);

        let input = fixture.engine.poll_input().unwrap();
        assert_eq!(input.data, FRAME);
        assert_eq!(input.payload_type.as_str(), FRAME_TYPE);
        assert_eq!(
            fixture.client_kinds(),
            vec![EventKind::Accepted, EventKind::Sent]
        );
    }

    #[test]
    fn outcome_becomes_a_result_event_with_its_bytes() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        fixture.accept_frame(now);
        fixture.client_kinds();
        fixture.engine.poll_input().unwrap();

        fixture.engine.pass(&RESULT).unwrap();
        fixture.dispatcher.poll(now).unwrap();

        let event = fixture.dispatcher.poll_client_event().unwrap();
        assert_eq!(event.session, fixture.session);
        assert_eq!(event.record.kind, EventKind::Result);
        assert_eq!(event.record.forward, Forward::Pass);
        let result = event.result.unwrap();
        assert_eq!(&*result.bytes, RESULT);
        assert_eq!(result.payload_type.as_str(), RESULT_TYPE);
    }

    #[test]
    fn engine_events_are_numbered_in_order() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let subscriber = services::open_engine_events(&node, &fixture.name)
            .unwrap()
            .subscriber_builder()
            .create()
            .unwrap();
        fixture.accept_frame(now);
        fixture.engine.poll_input().unwrap();
        fixture.engine.stop(&RESULT).unwrap();
        fixture.dispatcher.poll(now).unwrap();

        let events: Vec<(u64, EventKind)> = std::iter::from_fn(|| subscriber.receive().unwrap())
            .map(|sample| (sample.user_header().seq, sample.user_header().kind))
            .collect();

        assert_eq!(
            events,
            vec![
                (1, EventKind::Accepted),
                (2, EventKind::Sent),
                (3, EventKind::Result),
            ]
        );
    }

    #[test]
    fn subscriber_is_woken_for_the_engines_events() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        let (subscribed, waiting) = std::sync::mpsc::channel();
        let name = fixture.name.clone();
        let subscriber = std::thread::spawn(move || {
            let mut subscription = Subscription::engine(&name).unwrap();
            subscribed.send(()).unwrap();
            subscription.wait(LONG_WAIT).unwrap();
            let Event::Published(event) = subscription.poll().unwrap() else {
                panic!("expected an event");
            };
            event.kind()
        });
        waiting.recv().unwrap();
        let started = Instant::now();

        fixture.accept_frame(now);

        assert_eq!(subscriber.join().unwrap(), EventKind::Accepted);
        // Woken by the event, not by the end of the wait.
        assert!(started.elapsed() < LONG_WAIT / 2);
    }

    #[test]
    fn engine_subscriber_reads_each_event_with_its_result() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        let mut subscription = Subscription::engine(&fixture.name).unwrap();
        fixture.accept_frame(now);
        fixture.engine.poll_input().unwrap();
        fixture.engine.pass(&RESULT).unwrap();
        fixture.dispatcher.poll(now).unwrap();

        let mut kinds = Vec::new();
        while let Ok(Event::Published(event)) = subscription.poll() {
            kinds.push(event.kind());
            if event.kind() == EventKind::Result {
                assert_eq!(event.result(), RESULT);
                assert_eq!(event.payload_type(), RESULT_TYPE);
            }
        }

        assert_eq!(
            kinds,
            vec![EventKind::Accepted, EventKind::Sent, EventKind::Result]
        );
    }

    #[test]
    fn derived_result_reaches_subscribers_and_the_client() {
        const DECISION: &[u8] = b"person";
        const DECISION_TYPE: &str = "test.v1.Decision";
        let now = Instant::now();
        let mut fixture = Fixture::new();
        let mut subscription = Subscription::engine(&fixture.name).unwrap();
        let id = InputId {
            producer: ProducerName::try_from("camera").unwrap(),
            session: CLIENT_SESSION,
            seq: 1,
        };

        fixture
            .engine
            .publish_derived(&id, "tracking", DECISION_TYPE, DECISION)
            .unwrap();
        fixture.dispatcher.wait([], LONG_WAIT).unwrap();
        fixture.dispatcher.poll(now).unwrap();

        let Event::Published(event) = subscription.poll().unwrap() else {
            panic!("expected an event");
        };
        assert_eq!(event.kind(), EventKind::Derived);
        assert_eq!(event.id(), &id);
        assert_eq!(event.result(), DECISION);
        assert_eq!(event.payload_type(), DECISION_TYPE);
        let event = fixture.dispatcher.poll_client_event().unwrap();
        assert_eq!(event.session, fixture.session);
        assert_eq!(event.record.kind, EventKind::Derived);
        assert_eq!(&*event.result.unwrap().bytes, DECISION);
    }

    #[test]
    fn unanswered_input_times_out() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        fixture.accept_frame(now);
        fixture.client_kinds();

        fixture.dispatcher.poll(now + TIMEOUT).unwrap();

        assert_eq!(fixture.client_kinds(), vec![EventKind::TimedOut]);
    }

    #[test]
    fn input_no_engine_received_times_out_and_frees_its_place() {
        let now = Instant::now();
        let Fixture {
            mut dispatcher,
            engine,
            session,
            ..
        } = Fixture::new();
        drop(engine);

        // More inputs than the engine has places: each one is still delivered, because the one
        // before it gave its place back.
        for seq in 1..=services::ENGINE_DELIVERY_LIMIT as u64 + 1 {
            assert!(send_frame(&mut dispatcher, session, seq, now).is_err());
            assert_eq!(
                client_kinds(&mut dispatcher),
                vec![EventKind::Accepted, EventKind::Sent, EventKind::TimedOut]
            );
        }
    }

    #[test]
    fn delivery_wakes_the_engine() {
        let now = Instant::now();
        let mut fixture = Fixture::new();

        fixture.accept_frame(now);

        fixture.engine.wait(TIMEOUT).unwrap();
    }

    #[test]
    fn outcome_ends_the_servers_wait() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        fixture.accept_frame(now);
        fixture.engine.poll_input().unwrap();
        fixture.engine.stop(&[]).unwrap();

        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let other = services::open_server_input_ready(&node, &unique("server"))
            .unwrap()
            .listener_builder()
            .create()
            .unwrap();

        let started = Instant::now();
        fixture.dispatcher.wait([&other], LONG_WAIT).unwrap();

        assert!(started.elapsed() < LONG_WAIT);
    }

    #[test]
    fn stopped_engine_is_noticed_and_its_input_times_out() {
        let now = Instant::now();
        let Fixture {
            mut dispatcher,
            engine,
            name,
            session,
        } = Fixture::new();
        send_frame(&mut dispatcher, session, 1, now).unwrap();
        dispatcher.check_engines(now).unwrap();
        client_kinds(&mut dispatcher);

        drop(engine);
        dispatcher.check_engines(now).unwrap();

        assert_eq!(client_kinds(&mut dispatcher), vec![EventKind::TimedOut]);
        // An engine started again under the same name gets the next input.
        let mut engine = Engine::new(&name, Some(RESULT_TYPE), MAX_OUTPUT_SIZE).unwrap();
        send_frame(&mut dispatcher, session, 2, now).unwrap();
        assert_eq!(engine.poll_input().unwrap().data, FRAME);
    }

    #[test]
    fn engine_that_stops_before_its_first_check_is_noticed() {
        let now = Instant::now();
        let Fixture {
            mut dispatcher,
            engine,
            session,
            ..
        } = Fixture::new();
        send_frame(&mut dispatcher, session, 1, now).unwrap();
        client_kinds(&mut dispatcher);

        drop(engine);
        dispatcher.check_engines(now).unwrap();

        assert_eq!(client_kinds(&mut dispatcher), vec![EventKind::TimedOut]);
    }

    #[test]
    fn running_engine_is_not_lost() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        fixture.accept_frame(now);
        fixture.client_kinds();

        fixture.dispatcher.check_engines(now).unwrap();
        fixture.dispatcher.check_engines(now).unwrap();

        assert!(fixture.client_kinds().is_empty());
    }

    #[test]
    fn lost_engine_times_out_its_inputs() {
        let now = Instant::now();
        let mut fixture = Fixture::new();
        fixture.accept_frame(now);
        fixture.client_kinds();
        let name = fixture.name.clone();

        fixture.dispatcher.engine_lost(&name, now).unwrap();

        assert_eq!(fixture.client_kinds(), vec![EventKind::TimedOut]);
    }

    #[test]
    fn engine_used_by_a_flow_cannot_be_removed() {
        let mut fixture = Fixture::new();
        let name = fixture.name.clone();

        assert!(fixture.dispatcher.remove_engine(&name).is_err());
        fixture.dispatcher.remove_flow(fixture.session).unwrap();
        assert!(fixture.dispatcher.remove_engine(&name).is_ok());
    }

    #[test]
    fn short_outcome_payload_is_rejected() {
        let outcome = OutcomeRecord::emit(
            OfferId::default(),
            InputId::default(),
            PayloadType::new(),
            PayloadType::new(),
            4,
            2,
        )
        .unwrap();

        assert!(split_outcome(&outcome, &[0; 5]).is_none());
        let (output, result) = split_outcome(&outcome, &[0; 6]).unwrap();
        assert_eq!(output.unwrap().bytes.len(), 4);
        assert_eq!(result.unwrap().bytes.len(), 2);
    }
}
