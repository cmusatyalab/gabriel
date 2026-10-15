//! The client's connection to the remote server, over QUIC. A network thread owns the connection:
//! it connects, and connects again after a loss, sends calls and inputs, and reads the stream of
//! events. The client thread talks to it over channels (see [`crate::net`]).
//!
//! Each control call uses its own bidirectional stream, and each input its own unidirectional
//! stream, which carries the input's header and then its bytes. The server sends every event of
//! the client's flows on one unidirectional stream, in order.

use std::{
    net::{Ipv4Addr, SocketAddr},
    sync::{Arc, mpsc},
    thread::JoinHandle,
    time::Duration,
};

use futures_util::StreamExt;
use iceoryx2::{port::listener::Listener, prelude::*};
use iceoryx2_bb_container::relocatable_option::RelocatableOption;
use metrics::{Counter, Gauge, counter, gauge};
use quinn::{Connection, ConnectionStats, Endpoint};
use tokio::sync::mpsc::{UnboundedReceiver, UnboundedSender, unbounded_channel};
use tokio_util::codec::FramedRead;
use tracing::{Instrument, debug, info, info_span, warn};

use crate::{
    Error, Result,
    client::server_link::{Received, ServerEvent, ServerLink},
    clock::Timestamp,
    ipc::types::{ClientEventHeader, CrossingHeader, CrossingKind, PayloadType},
    metrics as gabriel_metrics, naming,
    net::{self, Waker},
    routing::{
        ids::{InputId, OfferId},
        server_router::InstallFlow,
    },
    services,
    wire::{self, Call, Reply, Request, Response},
};

/// How long a control call waits for the remote server's reply. It covers a round trip on a slow
/// link, so it is longer than on one machine.
const CONTROL_TIMEOUT: Duration = Duration::from_secs(5);
/// How long the network thread waits before connecting again after the first failure.
const RECONNECT_MIN_BACKOFF: Duration = Duration::from_millis(100);
/// The longest the network thread waits before connecting again.
const RECONNECT_MAX_BACKOFF: Duration = Duration::from_secs(5);
/// How often the network thread records the connection's statistics.
const STATS_INTERVAL: Duration = Duration::from_secs(1);
/// Lets the system pick the client's port.
const ANY_PORT: SocketAddr = SocketAddr::new(std::net::IpAddr::V4(Ipv4Addr::UNSPECIFIED), 0);

/// What the client thread asks the network thread to do.
enum Command {
    /// Send a control call, and send its reply, or why there is none, on `reply`.
    Call {
        request: Vec<u8>,
        reply: mpsc::Sender<Result<Vec<u8>>>,
    },
    /// Send an input, or a skip notice, on a crossing.
    Send {
        header: CrossingHeader,
        data: Arc<[u8]>,
    },
}

/// What the network thread tells the client thread.
enum Update {
    Connected,
    /// Boxed, because an event's record is large.
    Event(Box<ServerEvent>),
    /// The connection was lost. Every offer on the server ends.
    Lost,
}

pub(super) struct RemoteServer {
    /// The client's name, which the server sends the flow's events to.
    client: String,
    addr: SocketAddr,
    /// `None` once dropped, which stops the network thread.
    requests: Option<UnboundedSender<Command>>,
    updates: mpsc::Receiver<Update>,
    /// Wakes the client thread after each update.
    ready: Listener<ipc::Service>,
    connected: bool,
    thread: Option<JoinHandle<()>>,
}

impl RemoteServer {
    /// Starts connecting the client `client` to the remote server at `addr`, whose certificate is
    /// `certificate`, in DER. The server may start later.
    pub(super) fn open(
        node: &Node<ipc::Service>,
        client: &str,
        addr: SocketAddr,
        certificate: &[u8],
    ) -> Result<Self> {
        let ready_service = naming::client_remote_ready_service(client)?;
        let ready = services::open_remote_ready(node, &ready_service)?
            .listener_builder()
            .create()
            .map_err(|e| net::error("failed to create the remote server's listener", e))?;
        let config = net::client_config(certificate)?;
        let (requests_tx, requests_rx) = unbounded_channel();
        let (updates_tx, updates_rx) = mpsc::channel();
        let span = info_span!("remote server", %addr);
        let ((), thread) = net::spawn(
            format!("gabriel-client-{client}-network"),
            ready_service,
            move |waker| {
                let mut endpoint =
                    Endpoint::client(ANY_PORT).map_err(|e| net::error("failed to bind", e))?;
                endpoint.set_default_client_config(config);
                let network = Network {
                    endpoint,
                    addr,
                    requests: requests_rx,
                    updates: updates_tx,
                    waker,
                };
                Ok(((), network.run().instrument(span)))
            },
        )?;
        Ok(Self {
            client: client.to_owned(),
            addr,
            requests: Some(requests_tx),
            updates: updates_rx,
            ready,
            connected: false,
            thread: Some(thread),
        })
    }

    /// Sends `call` to the server, and waits for its reply.
    fn call(&self, call: Call) -> Result<Reply> {
        if !self.connected {
            return Err(self.not_connected());
        }
        let (reply_tx, reply_rx) = mpsc::channel();
        let request = Command::Call {
            request: wire::encode(&Request::new(call))?,
            reply: reply_tx,
        };
        self.request(request)?;
        let response = reply_rx.recv_timeout(CONTROL_TIMEOUT).map_err(|_| {
            Error::Internal(format!(
                "remote server {} did not reply within {CONTROL_TIMEOUT:?}",
                self.addr
            ))
        })??;
        wire::decode::<Response>(&response)?
    }

    fn send_on_crossing(&self, header: CrossingHeader, data: Arc<[u8]>) -> Result<()> {
        if !self.connected {
            return Err(self.not_connected());
        }
        self.request(Command::Send { header, data })
    }

    fn request(&self, request: Command) -> Result<()> {
        self.requests
            .as_ref()
            .and_then(|requests| requests.send(request).ok())
            .ok_or_else(|| Error::Internal(String::from("the network thread stopped")))
    }

    fn not_connected(&self) -> Error {
        Error::Internal(format!("not connected to remote server {}", self.addr))
    }

    fn unexpected(&self, reply: &Reply) -> Error {
        Error::Internal(format!(
            "remote server {} sent an unexpected reply: {reply:?}",
            self.addr
        ))
    }
}

impl ServerLink for RemoteServer {
    fn install(&self, spec: InstallFlow) -> Result<u64> {
        let call = Call::Install {
            client: self.client.clone(),
            spec,
        };
        match self.call(call)? {
            Reply::Installed { session } => Ok(session),
            reply => Err(self.unexpected(&reply)),
        }
    }

    fn remove_flow(&self, session: u64) -> Result<()> {
        let call = Call::RemoveFlow {
            client: self.client.clone(),
            session,
        };
        match self.call(call)? {
            Reply::Removed => Ok(()),
            reply => Err(self.unexpected(&reply)),
        }
    }

    fn send(
        &self,
        flow: u64,
        offer: OfferId,
        id: InputId,
        payload_type: PayloadType,
        data: Arc<[u8]>,
    ) -> Result<()> {
        let header = CrossingHeader {
            kind: CrossingKind::Input,
            flow,
            offer,
            id,
            payload_type,
        };
        self.send_on_crossing(header, data)
    }

    fn send_skipped(&self, flow: u64, offer: OfferId, id: InputId) -> Result<()> {
        let header = CrossingHeader {
            kind: CrossingKind::Skipped,
            flow,
            offer,
            id,
            payload_type: PayloadType::new(),
        };
        self.send_on_crossing(header, Arc::from([]))
    }

    /// The listener the network thread signals after each update.
    fn ready(&self) -> &Listener<ipc::Service> {
        &self.ready
    }

    fn take_events(&mut self) -> Result<Received> {
        // Without this the waitset keeps reporting the same notifications.
        self.ready
            .try_wait(|_| {})
            .map_err(|e| net::error("failed to drain network wake-ups", e))?;
        let mut received = Received::default();
        for update in self.updates.try_iter() {
            match update {
                Update::Connected => self.connected = true,
                Update::Event(event) => received.events.push(*event),
                Update::Lost => {
                    self.connected = false;
                    received.lost = true;
                }
            }
        }
        Ok(received)
    }

    /// Losses arrive with the events, as soon as the network thread notices them.
    fn check(&mut self) -> Result<bool> {
        Ok(false)
    }

    fn is_connected(&self) -> bool {
        self.connected
    }
}

impl Drop for RemoteServer {
    fn drop(&mut self) {
        // Closing the request channel stops the network thread, which closes the connection.
        drop(self.requests.take());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// The network thread's state.
struct Network {
    endpoint: Endpoint,
    addr: SocketAddr,
    requests: UnboundedReceiver<Command>,
    updates: mpsc::Sender<Update>,
    waker: Waker,
}

impl Network {
    /// Connects, serves the connection until it is lost, and connects again, until the client
    /// stops.
    async fn run(mut self) {
        let mut backoff = RECONNECT_MIN_BACKOFF;
        loop {
            // A clone, so that requests can be answered while connecting.
            let connect = connect(self.endpoint.clone(), self.addr);
            let Some(connected) = self.without_connection(connect).await else {
                break;
            };
            match connected {
                Ok(connection) => {
                    info!("connected");
                    backoff = RECONNECT_MIN_BACKOFF;
                    if self.serve(connection).await.is_err() {
                        break;
                    }
                }
                // Tried again until the server is up, so not worth a warning each time.
                Err(ConnectFailure::Handshake(quinn::ConnectionError::TimedOut)) => {
                    debug!("failed to connect: no answer");
                }
                // The server answered, but the connection failed, e.g. because its certificate is
                // not the one this client trusts. Trying again will not help without a fix.
                Err(e) => warn!(error = %e, "failed to connect"),
            }
            if self
                .without_connection(tokio::time::sleep(backoff))
                .await
                .is_none()
            {
                break;
            }
            backoff = (backoff * 2).min(RECONNECT_MAX_BACKOFF);
        }
        self.endpoint.close(net::CLOSED, b"client stopped");
        let _ = tokio::time::timeout(net::CLOSE_TIMEOUT, self.endpoint.wait_idle()).await;
    }

    /// Runs `until` while there is no connection. Calls fail at once, and inputs are dropped: the
    /// client sends none while it is not connected. Returns `None` if the client stopped.
    async fn without_connection<T>(&mut self, until: impl Future<Output = T>) -> Option<T> {
        tokio::pin!(until);
        loop {
            tokio::select! {
                value = &mut until => return Some(value),
                request = self.requests.recv() => match request? {
                    Command::Call { reply, .. } => {
                        let _ = reply.send(Err(Error::Internal(String::from("not connected"))));
                    }
                    Command::Send { .. } => {}
                },
            }
        }
    }

    /// Serves `connection` until it is lost, which returns `Ok`, or the client stops, which
    /// returns `Err`.
    async fn serve(&mut self, connection: Connection) -> std::result::Result<(), ()> {
        self.update(Update::Connected);
        let (events_tx, mut events) = unbounded_channel();
        let reader = tokio::spawn(read_events(connection.clone(), events_tx));
        let mut metrics = ConnectionMetrics::new(self.addr);
        let mut stats_timer = tokio::time::interval(STATS_INTERVAL);
        let outcome = loop {
            tokio::select! {
                _ = stats_timer.tick() => metrics.record(connection.stats()),
                request = self.requests.recv() => {
                    let Some(request) = request else {
                        connection.close(net::CLOSED, b"client stopped");
                        break Err(());
                    };
                    let connection = connection.clone();
                    match request {
                        Command::Call { request, reply } => {
                            tokio::spawn(async move {
                                let _ = reply.send(call(&connection, &request).await);
                            });
                        }
                        Command::Send { header, data } => {
                            tokio::spawn(async move {
                                // The input's crossing times out on the client.
                                if let Err(e) = send_input(&connection, &header, &data).await {
                                    debug!(error = %e, "failed to send an input");
                                }
                            });
                        }
                    }
                }
                event = events.recv() => match event {
                    Some(event) => {
                        // Each wake-up carries every event that has arrived.
                        let _ = self.updates.send(Update::Event(Box::new(event)));
                        while let Ok(event) = events.try_recv() {
                            let _ = self.updates.send(Update::Event(Box::new(event)));
                        }
                        self.waker.wake();
                    }
                    // The stream of events ended or broke, so the client cannot tell which
                    // offers are open.
                    None => {
                        connection.close(net::BROKEN, b"event stream broken");
                        break Ok(());
                    }
                },
                error = connection.closed() => {
                    warn!(%error, "lost the connection");
                    break Ok(());
                }
            }
        };
        reader.abort();
        metrics.record(connection.stats());
        metrics.connected.set(0.0);
        if outcome.is_ok() {
            self.update(Update::Lost);
        }
        outcome
    }

    fn update(&self, update: Update) {
        // The client thread only stops after it dropped the request channel, which stops this
        // thread first, so the update has a reader.
        let _ = self.updates.send(update);
        self.waker.wake();
    }
}

/// The metrics of one connection to the remote server (`crate::metrics`). quinn counts from 0 on
/// each connection, so the counters add what changed since the statistics last recorded.
struct ConnectionMetrics {
    connected: Gauge,
    rtt: Gauge,
    congestion_window: Gauge,
    lost_packets: Counter,
    congestion_events: Counter,
    sent_bytes: Counter,
    received_bytes: Counter,
    last: ConnectionStats,
}

impl ConnectionMetrics {
    fn new(server: SocketAddr) -> Self {
        let labels = [("server", server.to_string())];
        let metrics = Self {
            connected: gauge!(gabriel_metrics::REMOTE_CONNECTED, &labels),
            rtt: gauge!(gabriel_metrics::REMOTE_RTT, &labels),
            congestion_window: gauge!(gabriel_metrics::REMOTE_CONGESTION_WINDOW, &labels),
            lost_packets: counter!(gabriel_metrics::REMOTE_LOST_PACKETS, &labels),
            congestion_events: counter!(gabriel_metrics::REMOTE_CONGESTION_EVENTS, &labels),
            sent_bytes: counter!(gabriel_metrics::REMOTE_SENT_BYTES, &labels),
            received_bytes: counter!(gabriel_metrics::REMOTE_RECEIVED_BYTES, &labels),
            last: ConnectionStats::default(),
        };
        metrics.connected.set(1.0);
        metrics
    }

    fn record(&mut self, stats: ConnectionStats) {
        let (path, last) = (stats.path, self.last.path);
        self.rtt.set(path.rtt.as_secs_f64());
        self.congestion_window.set(path.cwnd as f64);
        self.lost_packets
            .increment(path.lost_packets.saturating_sub(last.lost_packets));
        self.congestion_events.increment(
            path.congestion_events
                .saturating_sub(last.congestion_events),
        );
        self.sent_bytes
            .increment(stats.udp_tx.bytes.saturating_sub(self.last.udp_tx.bytes));
        self.received_bytes
            .increment(stats.udp_rx.bytes.saturating_sub(self.last.udp_rx.bytes));
        self.last = stats;
    }
}

/// Why connecting failed.
#[derive(Debug, thiserror::Error)]
enum ConnectFailure {
    /// The connection could not be started.
    #[error(transparent)]
    Start(#[from] quinn::ConnectError),
    /// The handshake failed.
    #[error(transparent)]
    Handshake(#[from] quinn::ConnectionError),
}

async fn connect(
    endpoint: Endpoint,
    addr: SocketAddr,
) -> std::result::Result<Connection, ConnectFailure> {
    Ok(endpoint.connect(addr, net::SERVER_NAME)?.await?)
}

/// Sends a control call on its own stream, and returns the reply.
async fn call(connection: &Connection, request: &[u8]) -> Result<Vec<u8>> {
    let (mut send, mut recv) = connection
        .open_bi()
        .await
        .map_err(|e| net::error("failed to open a stream", e))?;
    send.write_all(request)
        .await
        .map_err(|e| net::error("failed to send a call", e))?;
    send.finish()
        .map_err(|e| net::error("failed to send a call", e))?;
    recv.read_to_end(net::MAX_CONTROL_SIZE)
        .await
        .map_err(|e| net::error("failed to read a reply", e))
}

/// Sends an input on its own stream: its header, then its bytes.
async fn send_input(connection: &Connection, header: &CrossingHeader, data: &[u8]) -> Result<()> {
    let mut send = connection
        .open_uni()
        .await
        .map_err(|e| net::error("failed to open a stream", e))?;
    send.write_all(&wire::encode(header)?)
        .await
        .map_err(|e| net::error("failed to send an input", e))?;
    send.write_all(data)
        .await
        .map_err(|e| net::error("failed to send an input", e))?;
    send.finish()
        .map_err(|e| net::error("failed to send an input", e))
}

/// Reads the stream of events, stamps each with when it arrived, and passes it on, until the
/// stream ends or breaks.
async fn read_events(connection: Connection, events: UnboundedSender<ServerEvent>) {
    let stream = match connection.accept_uni().await {
        Ok(stream) => stream,
        Err(e) => {
            debug!(error = %e, "no event stream");
            return;
        }
    };
    let mut frames = FramedRead::new(stream, net::event_codec());
    while let Some(frame) = frames.next().await {
        let received_at = Timestamp::now();
        let event = frame
            .map_err(|e| net::error("failed to read an event", e))
            .and_then(|frame| {
                let (header, payload) = wire::decode_with_payload::<ClientEventHeader>(&frame)?;
                let (mut record, forwarded, result) = header.split(payload)?;
                record.received_at = RelocatableOption::Some(received_at);
                Ok(ServerEvent {
                    record,
                    result: Box::from(result),
                    forwarded,
                })
            });
        match event {
            Ok(event) => {
                if events.send(event).is_err() {
                    return;
                }
            }
            Err(e) => {
                warn!(error = %e, "broken event stream");
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use std::{collections::HashMap, os::unix::fs::PermissionsExt, time::Instant};

    use metrics_util::debugging::{DebugValue, DebuggingRecorder};

    use super::*;
    use crate::{
        Engine, EventKind, Server,
        ipc::types::{FlowName, Forward},
        routing::{
            ids::{EdgeId, NodeId, ProducerName},
            server_router::{CrossingSpec, EdgeSpec},
        },
        test_util::unique,
    };

    /// Long enough to connect and exchange a few messages over loopback.
    const NETWORK_TIMEOUT: Duration = Duration::from_secs(5);
    /// How long a test waits between looks at what arrived.
    const POLL_INTERVAL: Duration = Duration::from_millis(5);
    const MAX_OUTPUT_SIZE: usize = 16;
    const CROSSING: EdgeId = EdgeId(1);
    const DETECTOR: NodeId = NodeId(0);
    const CAMERA: NodeId = NodeId(1);
    const EDGE_TIMEOUT: Duration = Duration::from_secs(1);
    const INPUT: &[u8] = &[1, 2, 3, 4];
    const RESULT: &[u8] = &[9, 8];

    /// A server that accepts remote clients, with one engine, and a client node.
    struct Fixture {
        server: Server,
        engine: Engine,
        detector: String,
        node: Node<ipc::Service>,
    }

    impl Fixture {
        fn new() -> Self {
            let detector = unique("detector");
            let engine = Engine::new(&detector, None, MAX_OUTPUT_SIZE).unwrap();
            let mut server = Server::new(&unique("server"), Some("127.0.0.1:0"), None).unwrap();
            server.add_engine(&detector).unwrap();
            let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
            Self {
                server,
                engine,
                detector,
                node,
            }
        }

        /// A link to the server, once it is connected.
        fn connect(&self) -> RemoteServer {
            let mut remote = RemoteServer::open(
                &self.node,
                &unique("client"),
                self.server.remote_addr().unwrap(),
                self.server.certificate().unwrap(),
            )
            .unwrap();
            wait_until(|| {
                remote.take_events().unwrap();
                remote.is_connected()
            });
            remote
        }

        /// A flow whose camera sends to the detector over one crossing.
        fn flow(&self) -> InstallFlow {
            InstallFlow {
                flow: String::from(FLOW),
                engines: vec![(DETECTOR, self.detector.clone())],
                edges: vec![],
                crossings: vec![CrossingSpec {
                    id: CROSSING,
                    targets: vec![EdgeSpec {
                        id: EdgeId(0),
                        from: CAMERA,
                        to: DETECTOR,
                        timeout: EDGE_TIMEOUT,
                    }],
                }],
                client_sources: vec![],
            }
        }
    }

    const FLOW: &str = "tracking";

    fn wait_until(mut done: impl FnMut() -> bool) {
        let deadline = Instant::now() + NETWORK_TIMEOUT;
        while !done() {
            assert!(Instant::now() < deadline, "timed out");
            std::thread::sleep(POLL_INTERVAL);
        }
    }

    fn input_id() -> InputId {
        InputId {
            producer: ProducerName::try_from("camera").unwrap(),
            session: 1,
            seq: 1,
        }
    }

    fn offer() -> OfferId {
        OfferId {
            session: 1,
            edge: CROSSING,
            seq: 1,
        }
    }

    #[test]
    fn input_reaches_a_remote_engine_and_its_events_come_back() {
        let mut fixture = Fixture::new();
        let mut remote = fixture.connect();
        let session = remote.install(fixture.flow()).unwrap();

        remote
            .send(
                session,
                offer(),
                input_id(),
                PayloadType::new(),
                Arc::from(INPUT),
            )
            .unwrap();
        fixture.engine.wait(NETWORK_TIMEOUT).unwrap();
        let input = fixture.engine.poll_input().unwrap();
        assert_eq!(input.data, INPUT);
        fixture.engine.pass(RESULT).unwrap();

        let mut events = Vec::new();
        wait_until(|| {
            events.extend(remote.take_events().unwrap().events);
            events
                .iter()
                .any(|event| event.record.kind == EventKind::Result)
        });
        let kinds: Vec<EventKind> = events.iter().map(|event| event.record.kind).collect();
        assert_eq!(
            kinds,
            [EventKind::Accepted, EventKind::Sent, EventKind::Result]
        );
        let result = &events[2];
        assert_eq!(&*result.result, RESULT);
        assert_eq!(result.record.forward, Forward::Pass);
        assert_eq!(result.record.flow, FlowName::try_from(FLOW).unwrap());
        assert!(
            events
                .iter()
                .all(|event| event.record.received_at.as_option_ref().is_some())
        );
    }

    #[test]
    fn calls_fail_at_once_without_a_server() {
        let fixture = Fixture::new();
        let mut remote = RemoteServer::open(
            &fixture.node,
            &unique("client"),
            // Nothing listens on the discard port of loopback.
            SocketAddr::from((Ipv4Addr::LOCALHOST, 9)),
            fixture.server.certificate().unwrap(),
        )
        .unwrap();

        remote.take_events().unwrap();
        let started = Instant::now();
        let installed = remote.install(fixture.flow());

        assert!(installed.is_err());
        assert!(started.elapsed() < CONTROL_TIMEOUT);
        assert!(!remote.is_connected());
    }

    #[test]
    fn untrusted_server_is_not_connected_to() {
        let fixture = Fixture::new();
        let other = Server::new(&unique("server"), Some("127.0.0.1:0"), None).unwrap();
        let mut remote = RemoteServer::open(
            &fixture.node,
            &unique("client"),
            fixture.server.remote_addr().unwrap(),
            other.certificate().unwrap(),
        )
        .unwrap();

        // Long enough for a few attempts to connect.
        std::thread::sleep(RECONNECT_MAX_BACKOFF / 10);
        remote.take_events().unwrap();

        assert!(!remote.is_connected());
    }

    #[test]
    fn restarted_server_with_an_identity_is_reconnected_to() {
        let dir = tempfile::tempdir().unwrap();
        // iceoryx2 changes the process's umask while it binds a socket, and parallel tests can
        // leave it changed.
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let server = Server::new(&unique("server"), Some("127.0.0.1:0"), Some(dir.path())).unwrap();
        let addr = server.remote_addr().unwrap();
        let mut remote = RemoteServer::open(
            &node,
            &unique("client"),
            addr,
            server.certificate().unwrap(),
        )
        .unwrap();
        wait_until(|| {
            remote.take_events().unwrap();
            remote.is_connected()
        });

        drop(server);
        wait_until(|| remote.take_events().unwrap().lost);
        let _restarted =
            Server::new(&unique("server"), Some(&addr.to_string()), Some(dir.path())).unwrap();

        wait_until(|| {
            remote.take_events().unwrap();
            remote.is_connected()
        });
    }

    #[test]
    fn stopped_server_is_noticed() {
        let fixture = Fixture::new();
        let mut remote = fixture.connect();
        let Fixture { server, engine, .. } = fixture;

        drop(server);
        drop(engine);

        wait_until(|| remote.take_events().unwrap().lost);
        assert!(!remote.is_connected());
    }

    #[test]
    fn flows_of_a_lost_client_are_removed() {
        let mut fixture = Fixture::new();
        let remote = fixture.connect();
        remote.install(fixture.flow()).unwrap();
        assert!(fixture.server.remove_engine(&fixture.detector).is_err());

        drop(remote);

        wait_until(|| fixture.server.remove_engine(&fixture.detector).is_ok());
    }

    #[test]
    fn only_the_installing_client_removes_a_flow() {
        let fixture = Fixture::new();
        let owner = fixture.connect();
        let other = fixture.connect();
        let session = owner.install(fixture.flow()).unwrap();

        assert!(matches!(
            other.remove_flow(session),
            Err(Error::InvalidArgument(_))
        ));
        owner.remove_flow(session).unwrap();
    }

    #[test]
    fn connection_metrics_add_what_changed_across_connections() {
        let recorder = DebuggingRecorder::new();
        let snapshotter = recorder.snapshotter();
        let server = SocketAddr::from((Ipv4Addr::LOCALHOST, 4433));
        metrics::with_local_recorder(&recorder, || {
            let mut stats = ConnectionStats::default();
            stats.path.lost_packets = 3;
            stats.udp_tx.bytes = 100;
            let mut first = ConnectionMetrics::new(server);
            first.record(stats);
            first.record(stats);
            first.connected.set(0.0);
            // quinn counts from 0 on the next connection.
            stats.path.lost_packets = 2;
            stats.udp_tx.bytes = 50;
            ConnectionMetrics::new(server).record(stats);
        });

        let values: HashMap<String, DebugValue> = snapshotter
            .snapshot()
            .into_vec()
            .into_iter()
            .map(|(key, _, _, value)| (key.key().name().to_owned(), value))
            .collect();
        assert_eq!(
            values[gabriel_metrics::REMOTE_LOST_PACKETS],
            DebugValue::Counter(5)
        );
        assert_eq!(
            values[gabriel_metrics::REMOTE_SENT_BYTES],
            DebugValue::Counter(150)
        );
        assert_eq!(
            values[gabriel_metrics::REMOTE_CONNECTED],
            DebugValue::Gauge(1.0.into())
        );
    }
}
