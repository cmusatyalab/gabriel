//! Server: runs the flows installed on it, and delivers their inputs to its engines. Its work runs
//! on its own thread.

mod dispatch;
mod worker;

use std::{sync::mpsc, thread::JoinHandle};

use crate::{
    Error, Result,
    ipc::types::PayloadType,
    routing::{
        ids::{InputId, OfferId},
        server_router::InstallFlow,
    },
    server::worker::{ClientInput, Command, EventSink},
};

/// Delivers the inputs of the flows installed on it to a dynamic set of engines.
#[derive(Debug)]
pub struct Server {
    commands: Option<mpsc::Sender<Command>>,
    thread: Option<JoinHandle<Result<()>>>,
}

impl Server {
    /// Creates the server `name`. `bind_addr` is the network address to accept remote clients
    /// on, or `None` for a local server, which only serves clients on its machine.
    pub fn new(name: &str, bind_addr: Option<&str>) -> Result<Self> {
        if let Some(addr) = bind_addr {
            return Err(Error::InvalidArgument(format!(
                "cannot accept remote clients on {addr:?}: not supported yet"
            )));
        }
        // No client transport exists yet, so client events have nowhere to go.
        Self::with_sink(name, Box::new(|_| {}))
    }

    /// Like [`Server::new`] for a local server, but hands every event for a client to `sink`.
    /// Tests use it until the client transports exist.
    fn with_sink(name: &str, sink: EventSink) -> Result<Self> {
        let (commands, thread) = worker::spawn(name, sink)?;
        Ok(Self {
            commands: Some(commands),
            thread: Some(thread),
        })
    }

    /// Starts serving the local engine `name`. The engine may start before or after this call.
    pub fn add_engine(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::AddEngine {
            name: name.to_string(),
            reply,
        })
    }

    /// Stops serving the engine `name`. Fails while an installed flow uses it.
    pub fn remove_engine(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::RemoveEngine {
            name: name.to_string(),
            reply,
        })
    }

    /// Installs a client's part of a flow, and returns the flow's session.
    pub(crate) fn install(&mut self, spec: InstallFlow) -> Result<u64> {
        self.request(|reply| Command::Install { spec, reply })
    }

    /// Removes the flow `session`.
    pub(crate) fn remove_flow(&mut self, session: u64) -> Result<()> {
        self.request(|reply| Command::RemoveFlow { session, reply })
    }

    /// A client sent the input `id` on a crossing of the flow `session`, as its offer `offer`.
    pub(crate) fn accept(
        &mut self,
        session: u64,
        offer: OfferId,
        id: InputId,
        payload_type: PayloadType,
        bytes: &[u8],
    ) -> Result<()> {
        let input = ClientInput {
            session,
            offer,
            id,
            payload_type,
            bytes: bytes.to_vec(),
        };
        self.request(|reply| Command::Accept { input, reply })
    }

    /// Sends a command to the server thread and waits for its reply.
    fn request<R>(&self, command: impl FnOnce(mpsc::Sender<Result<R>>) -> Command) -> Result<R> {
        let (reply, reply_rx) = mpsc::channel();
        let stopped = || Error::Internal(String::from("server thread stopped"));
        match self.commands.as_ref() {
            None => return Err(stopped()),
            Some(commands) => commands.send(command(reply)).map_err(|_| stopped())?,
        }
        reply_rx.recv().map_err(|_| stopped())?
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        // Dropping the only command sender disconnects the channel, which makes the server thread
        // return from its loop. Then wait for it to finish.
        drop(self.commands.take());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::{
        Engine,
        ipc::types::{EventKind, payload_type},
        routing::{
            ids::{EdgeId, NodeId, ProducerName},
            server_router::{CrossingSpec, EdgeSpec},
        },
        server::dispatch::ClientEvent,
        test_util::unique,
    };

    const CLIENT_SESSION: u64 = 7;
    const MAX_OUTPUT_SIZE: usize = 16;
    const OFFER_TIMEOUT: Duration = Duration::from_secs(5);
    /// Far longer than anything here takes, so a test fails rather than hangs.
    const WAIT: Duration = Duration::from_secs(5);
    const FRAME: [u8; 4] = [1, 2, 3, 4];
    const FRAME_TYPE: &str = "test.v1.Frame";
    const SOURCE: NodeId = NodeId(0);
    const DETECTOR: NodeId = NodeId(1);
    const CROSSING: EdgeId = EdgeId(10);
    const TO_DETECTOR: EdgeId = EdgeId(11);

    /// A server with one engine and a flow that feeds it over a crossing, and the events the
    /// server sends to clients.
    struct Fixture {
        server: Server,
        engine: Engine,
        session: u64,
        events: mpsc::Receiver<ClientEvent>,
    }

    impl Fixture {
        fn new() -> Self {
            let (events_tx, events) = mpsc::channel();
            let mut server = Server::with_sink(
                &unique("server"),
                Box::new(move |event| {
                    let _ = events_tx.send(event);
                }),
            )
            .unwrap();
            let name = unique("detector");
            let engine = Engine::new(&name, None, MAX_OUTPUT_SIZE).unwrap();
            server.add_engine(&name).unwrap();
            let session = server
                .install(InstallFlow {
                    flow: String::from("tracking"),
                    engines: vec![(DETECTOR, name)],
                    edges: vec![],
                    crossings: vec![CrossingSpec {
                        id: CROSSING,
                        targets: vec![EdgeSpec {
                            id: TO_DETECTOR,
                            from: SOURCE,
                            to: DETECTOR,
                            timeout: OFFER_TIMEOUT,
                        }],
                    }],
                })
                .unwrap();
            Self {
                server,
                engine,
                session,
                events,
            }
        }

        /// The client sends its `seq`th frame.
        fn send_frame(&mut self, seq: u64) {
            self.server
                .accept(
                    self.session,
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
                )
                .unwrap();
        }

        /// The engine waits for its next input, and returns its seq.
        fn next_input(&mut self) -> u64 {
            self.engine.wait(WAIT).unwrap();
            self.engine.poll_input().unwrap().id.seq
        }

        /// The next `count` events for the client, as `(kind, input seq)`.
        fn events(&self, count: usize) -> Vec<(EventKind, u64)> {
            (0..count)
                .map(|_| {
                    let event = self.events.recv_timeout(WAIT).unwrap();
                    (event.record.kind, event.record.id.seq)
                })
                .collect()
        }
    }

    #[test]
    fn input_reaches_the_engine_and_its_result_reaches_the_client() {
        let mut fixture = Fixture::new();

        fixture.send_frame(1);
        assert_eq!(fixture.next_input(), 1);
        fixture.engine.stop(&[]).unwrap();

        assert_eq!(
            fixture.events(3),
            vec![
                (EventKind::Accepted, 1),
                (EventKind::Sent, 1),
                (EventKind::Result, 1)
            ]
        );
    }

    #[test]
    fn outcome_lets_the_engine_take_the_newest_waiting_input() {
        let mut fixture = Fixture::new();
        fixture.send_frame(1);
        assert_eq!(fixture.next_input(), 1);
        fixture.send_frame(2);
        fixture.send_frame(3);

        fixture.engine.stop(&[]).unwrap();

        assert_eq!(fixture.next_input(), 3);
    }

    #[test]
    fn engines_are_added_and_removed_once() {
        let mut server = Server::new(&unique("server"), None).unwrap();
        let name = unique("engine");

        server.add_engine(&name).unwrap();
        assert!(matches!(
            server.add_engine(&name),
            Err(Error::InvalidArgument(_))
        ));
        server.remove_engine(&name).unwrap();
        assert!(matches!(
            server.remove_engine(&name),
            Err(Error::InvalidArgument(_))
        ));
    }

    #[test]
    fn remote_clients_are_not_supported_yet() {
        let result = Server::new(&unique("server"), Some("127.0.0.1:4433"));

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn server_is_send() {
        fn assert_send<T: Send>() {}
        assert_send::<Server>();
    }
}
