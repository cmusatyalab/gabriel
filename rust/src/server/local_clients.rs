//! The server's ends of the services its local clients use: control calls, inputs sent on
//! crossings, and one event stream per client. It passes what arrives to the dispatcher, and sends
//! each client the events of the flows it installed.

use std::{
    collections::{HashMap, HashSet},
    time::Instant,
};

use iceoryx2::port::{
    listener::Listener, notifier::Notifier, publisher::Publisher, server::Server as ControlServer,
    subscriber::Subscriber,
};
use iceoryx2::prelude::*;
use tracing::{debug, info};

use crate::{
    Error, Result,
    ipc::types::{CrossingHeader, CrossingKind, EventRecord},
    server::{
        client_link::ClientLink,
        dispatch::{ClientEvent, Dispatcher},
    },
    services::{self, ClientEventPayload, ControlPayload, CrossingPayload},
    wire::{self, Call, Reply, Response},
};

/// The slice length the event publishers and the control server start with. They grow as needed.
const INITIAL_SLICE_LEN: usize = 4096;

/// The stream of events to one client.
struct EventStream {
    events: Publisher<ipc::Service, ClientEventPayload, EventRecord>,
    /// Wakes the client after each batch of events.
    ready: Notifier<ipc::Service>,
    /// The sequence number of the next event, so the client can tell whether it lost any.
    next_event: u64,
}

impl EventStream {
    fn open(node: &Node<ipc::Service>, client: &str) -> Result<Self> {
        let events = services::open_client_events(node, client)?
            .publisher_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .allocation_strategy(AllocationStrategy::PowerOfTwo)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create event publisher: {e}")))?;
        let ready = services::open_client_events_ready(node, client)?
            .notifier_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create event notifier: {e}")))?;
        Ok(Self {
            events,
            ready,
            next_event: 1,
        })
    }

    /// Numbers `event` and publishes it, with its result as the payload.
    fn publish(&mut self, event: ClientEvent) -> Result<()> {
        let bytes = event.result.as_ref().map_or(&[][..], |data| &data.bytes);
        let mut sample = self
            .events
            .loan_slice_uninit(bytes.len())
            .map_err(|e| Error::Internal(format!("failed to loan event: {e}")))?;
        *sample.user_header_mut() = event.record.numbered(self.next_event);
        self.next_event += 1;
        sample
            .write_from_slice(bytes)
            .send()
            .map_err(|e| Error::Internal(format!("failed to publish event: {e}")))?;
        Ok(())
    }

    fn wake(&self) -> Result<()> {
        self.ready
            .notify()
            .map_err(|e| Error::Internal(format!("failed to wake the client: {e}")))?;
        Ok(())
    }
}

pub(crate) struct LocalClients {
    control: ControlServer<ipc::Service, ControlPayload, (), ControlPayload, ()>,
    inputs: Subscriber<ipc::Service, CrossingPayload, CrossingHeader>,
    /// Wakes the server after a client sends an input.
    input_ready: Listener<ipc::Service>,
    /// The event stream of each client with an installed flow.
    streams: HashMap<String, EventStream>,
    /// The client that installed each flow, by the flow's session.
    owners: HashMap<u64, String>,
    /// The clients that were sent events since the last wake-up.
    unwoken: HashSet<String>,
}

impl LocalClients {
    /// Opens the services of the server `name`.
    pub(crate) fn open(node: &Node<ipc::Service>, name: &str) -> Result<Self> {
        let control = services::open_server_control(node, name)?
            .server_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .allocation_strategy(AllocationStrategy::PowerOfTwo)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create control server: {e}")))?;
        let inputs = services::open_server_input(node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create input subscriber: {e}")))?;
        let input_ready = services::open_server_input_ready(node, name)?
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create input listener: {e}")))?;
        Ok(Self {
            control,
            inputs,
            input_ready,
            streams: HashMap::new(),
            owners: HashMap::new(),
            unwoken: HashSet::new(),
        })
    }

    fn answer(&mut self, dispatcher: &mut Dispatcher, request: &[u8]) -> Response {
        match wire::decode_call(request)? {
            Call::Install { client, spec } => {
                if !self.streams.contains_key(&client) {
                    let stream = EventStream::open(dispatcher.node(), &client)?;
                    self.streams.insert(client.clone(), stream);
                }
                let flow = spec.flow.clone();
                let installed = dispatcher.install(spec);
                match installed {
                    Ok(session) => {
                        info!(client, flow, session, "installed flow");
                        self.owners.insert(session, client);
                        Ok(Reply::Installed { session })
                    }
                    Err(e) => {
                        debug!(client, flow, error = %e, "refused to install flow");
                        self.close_unused_stream(&client);
                        Err(e)
                    }
                }
            }
            Call::RemoveFlow { client, session } => {
                if self.owners.get(&session) != Some(&client) {
                    return Err(Error::InvalidArgument(format!(
                        "client {client:?} installed no flow {session}"
                    )));
                }
                dispatcher.remove_flow(session)?;
                info!(client, session, "removed flow");
                self.owners.remove(&session);
                self.close_unused_stream(&client);
                Ok(Reply::Removed)
            }
        }
    }

    /// Closes the event stream to `client` once it has no flows left.
    fn close_unused_stream(&mut self, client: &str) {
        if !self.owners.values().any(|owner| owner == client) {
            self.streams.remove(client);
            self.unwoken.remove(client);
        }
    }

    /// Answers every waiting control call. Returns the first error, after answering the rest.
    fn answer_calls(&mut self, dispatcher: &mut Dispatcher) -> Result<()> {
        let mut first_error = None;
        loop {
            let request = match self.control.receive() {
                Ok(Some(request)) => request,
                Ok(None) => break,
                Err(e) => {
                    first_error.get_or_insert(Error::Internal(format!(
                        "failed to receive a control call: {e}"
                    )));
                    break;
                }
            };
            let response = self.answer(dispatcher, request.payload());
            let replied = wire::encode(&response).and_then(|bytes| {
                request
                    .loan_slice_uninit(bytes.len())
                    .map_err(|e| Error::Internal(format!("failed to loan reply: {e}")))?
                    .write_from_slice(&bytes)
                    .send()
                    .map_err(|e| Error::Internal(format!("failed to send reply: {e}")))
            });
            if let Err(e) = replied {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    /// Passes every input the clients sent to the dispatcher, and every offer they skipped on a
    /// crossing. Returns the first error, after passing the rest.
    fn take_inputs(&mut self, dispatcher: &mut Dispatcher, now: Instant) -> Result<()> {
        // Consume the wake-ups, or the next wait returns at once for the same inputs.
        self.input_ready
            .try_wait(|_| {})
            .map_err(|e| Error::Internal(format!("failed to read input wake-ups: {e}")))?;
        let mut first_error = None;
        loop {
            let sample = match self.inputs.receive() {
                Ok(Some(sample)) => sample,
                Ok(None) => break,
                Err(e) => {
                    first_error
                        .get_or_insert(Error::Internal(format!("failed to receive input: {e}")));
                    break;
                }
            };
            let header = sample.user_header();
            let handled = match header.kind {
                CrossingKind::Input => dispatcher.accept(
                    header.flow,
                    header.offer,
                    header.id,
                    header.payload_type,
                    sample.payload(),
                    now,
                ),
                CrossingKind::Skipped => {
                    dispatcher.crossing_skipped(header.flow, header.offer, header.id, now)
                }
            };
            if let Err(e) = handled {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }
}

impl ClientLink for LocalClients {
    /// The listener clients signal after they send an input.
    fn ready(&self) -> &Listener<ipc::Service> {
        &self.input_ready
    }

    fn receive(&mut self, dispatcher: &mut Dispatcher, now: Instant) -> Result<()> {
        // Calls first, so that an input that follows its flow's install finds the flow.
        let answered = self.answer_calls(dispatcher);
        let taken = self.take_inputs(dispatcher, now);
        answered.and(taken)
    }

    fn owns(&self, session: u64) -> bool {
        self.owners.contains_key(&session)
    }

    fn send_event(&mut self, event: ClientEvent) -> Result<()> {
        let client = &self.owners[&event.session];
        let stream = self
            .streams
            .get_mut(client)
            .expect("a client with flows has a stream");
        self.unwoken.insert(client.clone());
        stream.publish(event)
    }

    fn flush(&mut self) -> Result<()> {
        let mut first_error = None;
        for client in self.unwoken.drain() {
            if let Err(e) = self.streams[&client].wake() {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        Engine,
        ipc::types::{EventKind, PayloadType},
        routing::{
            ids::{EdgeId, InputId, NodeId, OfferId, ProducerName},
            server_router::{CrossingSpec, EdgeSpec, InstallFlow},
        },
        test_util::unique,
        wire::{PROTOCOL_VERSION, Request},
    };

    /// A dispatcher with its local clients' services, and a raw control client.
    struct Fixture {
        name: String,
        dispatcher: Dispatcher,
        clients: LocalClients,
        control:
            iceoryx2::port::client::Client<ipc::Service, ControlPayload, (), ControlPayload, ()>,
    }

    impl Fixture {
        fn new() -> Self {
            let name = unique("server");
            let dispatcher = Dispatcher::new(&name).unwrap();
            let clients = LocalClients::open(dispatcher.node(), &name).unwrap();
            let control = services::open_server_control(dispatcher.node(), &name)
                .unwrap()
                .client_builder()
                .initial_max_slice_len(INITIAL_SLICE_LEN)
                .create()
                .unwrap();
            Self {
                name,
                dispatcher,
                clients,
                control,
            }
        }

        /// Sends `bytes` as a control call, and returns the server's reply.
        fn call(&mut self, bytes: &[u8]) -> Response {
            let pending = self
                .control
                .loan_slice_uninit(bytes.len())
                .unwrap()
                .write_from_slice(bytes)
                .send()
                .unwrap();
            self.clients.answer_calls(&mut self.dispatcher).unwrap();
            let response = pending.receive().unwrap().unwrap();
            wire::decode(response.payload()).unwrap()
        }
    }

    fn install(client: &str) -> Request {
        Request::new(Call::Install {
            client: String::from(client),
            spec: InstallFlow {
                flow: String::from("tracking"),
                engines: vec![],
                edges: vec![],
                crossings: vec![],
            },
        })
    }

    #[test]
    fn install_and_remove_are_answered() {
        let mut fixture = Fixture::new();
        let client = unique("client");

        let Ok(Reply::Installed { session }) =
            fixture.call(&wire::encode(&install(&client)).unwrap())
        else {
            panic!("install failed");
        };
        let removed = fixture
            .call(&wire::encode(&Request::new(Call::RemoveFlow { client, session })).unwrap());

        assert!(matches!(removed, Ok(Reply::Removed)));
    }

    #[test]
    fn only_the_installing_client_removes_a_flow() {
        let mut fixture = Fixture::new();
        let Ok(Reply::Installed { session }) =
            fixture.call(&wire::encode(&install(&unique("client"))).unwrap())
        else {
            panic!("install failed");
        };

        let removed = fixture.call(
            &wire::encode(&Request::new(Call::RemoveFlow {
                client: unique("other"),
                session,
            }))
            .unwrap(),
        );

        assert!(matches!(removed, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn call_of_another_version_is_refused() {
        let mut fixture = Fixture::new();
        let mut request = install(&unique("client"));
        request.version = PROTOCOL_VERSION + 1;

        let response = fixture.call(&wire::encode(&request).unwrap());

        assert!(matches!(response, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn crossing_skipped_by_a_client_is_told_to_the_engines_subscribers() {
        const MAX_OUTPUT_SIZE: usize = 16;
        const CROSSING: EdgeId = EdgeId(1);
        const DETECTOR: NodeId = NodeId(0);
        let mut fixture = Fixture::new();
        let detector = unique("detector");
        let _engine = Engine::new(&detector, None, MAX_OUTPUT_SIZE).unwrap();
        fixture.dispatcher.add_engine(&detector).unwrap();
        let mut request = install(&unique("client"));
        let Call::Install { spec, .. } = &mut request.call else {
            unreachable!("install() makes an install call");
        };
        spec.engines = vec![(DETECTOR, detector.clone())];
        spec.crossings = vec![CrossingSpec {
            id: CROSSING,
            targets: vec![EdgeSpec {
                id: EdgeId(0),
                from: NodeId(1),
                to: DETECTOR,
                timeout: std::time::Duration::from_secs(1),
            }],
        }];
        let Ok(Reply::Installed { session }) = fixture.call(&wire::encode(&request).unwrap())
        else {
            panic!("install failed");
        };
        let node = fixture.dispatcher.node();
        let subscriber = services::open_engine_events(node, &detector)
            .unwrap()
            .subscriber_builder()
            .create()
            .unwrap();
        let inputs = services::open_server_input(node, &fixture.name)
            .unwrap()
            .publisher_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .create()
            .unwrap();

        let mut sample = inputs.loan_slice_uninit(0).unwrap();
        *sample.user_header_mut() = CrossingHeader {
            kind: CrossingKind::Skipped,
            flow: session,
            offer: OfferId {
                session: 1,
                edge: CROSSING,
                seq: 1,
            },
            id: InputId {
                producer: ProducerName::try_from("camera").unwrap(),
                session: 1,
                seq: 1,
            },
            payload_type: PayloadType::new(),
        };
        sample.write_from_slice(&[]).send().unwrap();
        fixture
            .clients
            .take_inputs(&mut fixture.dispatcher, Instant::now())
            .unwrap();

        let event = subscriber.receive().unwrap().unwrap();
        assert_eq!(event.user_header().kind, EventKind::Skipped);
        assert!(subscriber.receive().unwrap().is_none());
    }

    #[test]
    fn malformed_call_is_refused() {
        let mut fixture = Fixture::new();

        let response = fixture.call(&[0xff; 3]);

        assert!(matches!(response, Err(Error::InvalidArgument(_))));
    }
}
