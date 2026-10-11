//! The server's connections to remote clients, over QUIC. A network thread accepts connections,
//! reads the calls and inputs on each, and writes each client's events on one stream, in order.
//! The server thread talks to it over channels (see [`crate::net`]).
//!
//! A flow belongs to the connection that installed it. When the connection is lost, the server
//! removes its flows; the client installs them again when it reconnects.

use std::{collections::HashMap, net::SocketAddr, sync::mpsc, thread::JoinHandle, time::Instant};

use bytes::Bytes;
use futures_util::SinkExt;
use iceoryx2::{port::listener::Listener, prelude::*};
use quinn::{Connection, Endpoint, Incoming, RecvStream, SendStream};
use tokio::sync::{
    mpsc::{Sender, UnboundedSender, error::TrySendError, unbounded_channel},
    oneshot,
};
use tokio_util::codec::FramedWrite;
use tracing::{Instrument, debug, info, info_span, warn};

use crate::{
    Error, Result,
    ipc::types::{CrossingHeader, CrossingKind},
    naming,
    net::{self, Waker},
    server::{
        client_link::ClientLink,
        dispatch::{ClientEvent, Dispatcher},
    },
    services,
    wire::{self, Call, Reply, Response},
};

/// How many encoded events wait for a client's link. If its queue fills, the link is too slow to
/// carry the client's events, and the server closes the connection: dropping events would break
/// the client's token accounting.
const EVENT_QUEUE_CAPACITY: usize = 4096;

/// Tells the server's connections apart.
type ConnectionId = u64;

/// What the network thread tells the server thread.
enum Update {
    Connected {
        id: ConnectionId,
        connection: Connection,
        events: Sender<Bytes>,
    },
    /// A control call, whose encoded reply goes on `reply`.
    Call {
        id: ConnectionId,
        request: Vec<u8>,
        reply: oneshot::Sender<Vec<u8>>,
    },
    /// An input or a skip notice: its header, then the input's bytes.
    Input {
        id: ConnectionId,
        message: Vec<u8>,
    },
    Lost {
        id: ConnectionId,
    },
}

/// One connected client.
struct Peer {
    connection: Connection,
    /// The encoded events waiting to be written.
    events: Sender<Bytes>,
    /// The sequence number of the next event.
    next_event: u64,
}

pub(crate) struct RemoteClients {
    endpoint: Endpoint,
    certificate: Vec<u8>,
    updates: mpsc::Receiver<Update>,
    /// Wakes the server thread after each update.
    ready: Listener<ipc::Service>,
    peers: HashMap<ConnectionId, Peer>,
    /// The connection that installed each flow, by the flow's session.
    owners: HashMap<u64, ConnectionId>,
    thread: Option<JoinHandle<()>>,
}

impl RemoteClients {
    /// Accepts remote clients of the server `name` on `addr`.
    pub(crate) fn open(node: &Node<ipc::Service>, name: &str, addr: SocketAddr) -> Result<Self> {
        let ready_service = naming::server_remote_ready_service(name)?;
        let ready = services::open_remote_ready(node, &ready_service)?
            .listener_builder()
            .create()
            .map_err(|e| net::error("failed to create the remote clients' listener", e))?;
        let (config, certificate) = net::server_config()?;
        let (updates_tx, updates_rx) = mpsc::channel();
        let ((endpoint, certificate), thread) = net::spawn(
            format!("gabriel-server-{name}-network"),
            ready_service,
            move |waker| {
                let endpoint = Endpoint::server(config, addr)
                    .map_err(|e| net::error(&format!("failed to listen on {addr}"), e))?;
                let span = info_span!("remote clients", addr = ?endpoint.local_addr().ok());
                let accepting = accept(endpoint.clone(), updates_tx, waker).instrument(span);
                Ok(((endpoint, certificate.to_vec()), accepting))
            },
        )?;
        Ok(Self {
            endpoint,
            certificate,
            updates: updates_rx,
            ready,
            peers: HashMap::new(),
            owners: HashMap::new(),
            thread: Some(thread),
        })
    }

    /// The address the server accepts remote clients on.
    pub(crate) fn local_addr(&self) -> Result<SocketAddr> {
        self.endpoint
            .local_addr()
            .map_err(|e| net::error("failed to read the local address", e))
    }

    /// The server's certificate, in DER, which its clients must trust.
    pub(crate) fn certificate(&self) -> &[u8] {
        &self.certificate
    }

    fn answer(
        &mut self,
        dispatcher: &mut Dispatcher,
        id: ConnectionId,
        request: &[u8],
    ) -> Response {
        match wire::decode_call(request)? {
            Call::Install { client, spec } => {
                let flow = spec.flow.clone();
                match dispatcher.install(spec) {
                    Ok(session) => {
                        info!(client, flow, session, "installed flow");
                        self.owners.insert(session, id);
                        Ok(Reply::Installed { session })
                    }
                    Err(e) => {
                        debug!(client, flow, error = %e, "refused to install flow");
                        Err(e)
                    }
                }
            }
            Call::RemoveFlow { client, session } => {
                if self.owners.get(&session) != Some(&id) {
                    return Err(Error::InvalidArgument(format!(
                        "client {client:?} installed no flow {session}"
                    )));
                }
                dispatcher.remove_flow(session)?;
                info!(client, session, "removed flow");
                self.owners.remove(&session);
                Ok(Reply::Removed)
            }
        }
    }

    fn take_input(&self, dispatcher: &mut Dispatcher, message: &[u8], now: Instant) -> Result<()> {
        let (header, data) = wire::decode_with_payload::<CrossingHeader>(message)?;
        match header.kind {
            CrossingKind::Input => dispatcher.accept(
                header.flow,
                header.offer,
                header.id,
                header.payload_type,
                data,
                now,
            ),
            CrossingKind::Skipped => {
                dispatcher.crossing_skipped(header.flow, header.offer, header.id, now)
            }
        }
    }

    /// Forgets the connection `id`, and removes the flows it installed.
    fn lost(&mut self, dispatcher: &mut Dispatcher, id: ConnectionId) -> Result<()> {
        self.peers.remove(&id);
        let mut first_error = None;
        let sessions: Vec<u64> = self
            .owners
            .iter()
            .filter(|&(_, &owner)| owner == id)
            .map(|(&session, _)| session)
            .collect();
        for session in sessions {
            self.owners.remove(&session);
            info!(session, "removed the flow of a lost client");
            if let Err(e) = dispatcher.remove_flow(session) {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }
}

impl ClientLink for RemoteClients {
    /// The listener the network thread signals after each update.
    fn ready(&self) -> &Listener<ipc::Service> {
        &self.ready
    }

    fn receive(&mut self, dispatcher: &mut Dispatcher, now: Instant) -> Result<()> {
        // Without this the waitset keeps reporting the same notifications.
        self.ready
            .try_wait(|_| {})
            .map_err(|e| net::error("failed to drain network wake-ups", e))?;
        let mut first_error = None;
        // Updates come in the order they happened on each connection, so a flow is installed
        // before its inputs arrive.
        while let Ok(update) = self.updates.try_recv() {
            let handled = match update {
                Update::Connected {
                    id,
                    connection,
                    events,
                } => {
                    self.peers.insert(
                        id,
                        Peer {
                            connection,
                            events,
                            next_event: 1,
                        },
                    );
                    Ok(())
                }
                Update::Call { id, request, reply } => {
                    let response = self.answer(dispatcher, id, &request);
                    wire::encode(&response).map(|bytes| {
                        // A client that is gone needs no reply.
                        let _ = reply.send(bytes);
                    })
                }
                Update::Input { id, message } if self.peers.contains_key(&id) => {
                    self.take_input(dispatcher, &message, now)
                }
                // An input from a connection that is already lost.
                Update::Input { .. } => Ok(()),
                Update::Lost { id } => self.lost(dispatcher, id),
            };
            if let Err(e) = handled {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    fn owns(&self, session: u64) -> bool {
        self.owners.contains_key(&session)
    }

    /// Queues the event for the network thread. A client that cannot keep up is disconnected.
    fn send_event(&mut self, event: ClientEvent) -> Result<()> {
        let id = self.owners[&event.session];
        // A client the server disconnected: its flows are removed once the loss is noticed.
        let Some(peer) = self.peers.get_mut(&id) else {
            return Ok(());
        };
        let result = event.result.as_ref().map_or(&[][..], |data| &data.bytes);
        let record = event.record.numbered(peer.next_event);
        peer.next_event += 1;
        let frame = wire::encode_with_payload(&record, result)?;
        match peer.events.try_send(Bytes::from(frame)) {
            Ok(()) => Ok(()),
            Err(TrySendError::Full(_)) => {
                peer.connection
                    .close(net::BROKEN, b"too slow to receive events");
                self.peers.remove(&id);
                Err(Error::Internal(String::from(
                    "a remote client fell behind on its events; disconnected it",
                )))
            }
            // The connection is closing; the loss will be noticed.
            Err(TrySendError::Closed(_)) => Ok(()),
        }
    }

    /// Events are written as soon as they are queued.
    fn flush(&mut self) -> Result<()> {
        Ok(())
    }
}

impl Drop for RemoteClients {
    fn drop(&mut self) {
        // Closing the endpoint ends the accept loop, which stops the network thread.
        self.endpoint.close(net::CLOSED, b"server stopped");
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

/// Accepts connections until the endpoint is closed, and passes what they receive to the server
/// thread.
async fn accept(endpoint: Endpoint, updates: mpsc::Sender<Update>, waker: Waker) {
    // The connections' tasks report here, because only this task can wake the server thread.
    let (updates_tx, mut updates_rx) = unbounded_channel();
    let mut next_id: ConnectionId = 0;
    loop {
        tokio::select! {
            incoming = endpoint.accept() => {
                let Some(incoming) = incoming else { break };
                next_id += 1;
                tokio::spawn(serve(incoming, next_id, updates_tx.clone()));
            }
            Some(update) = updates_rx.recv() => {
                // The server thread only stops after it closed the endpoint, so the updates have
                // a reader until then.
                let _ = updates.send(update);
                while let Ok(update) = updates_rx.try_recv() {
                    let _ = updates.send(update);
                }
                waker.wake();
            }
        }
    }
    let _ = tokio::time::timeout(net::CLOSE_TIMEOUT, endpoint.wait_idle()).await;
}

/// Serves one connection until it is lost.
async fn serve(incoming: Incoming, id: ConnectionId, updates: UnboundedSender<Update>) {
    let connection = match incoming.await {
        Ok(connection) => connection,
        Err(e) => {
            debug!(error = %e, "a client failed to connect");
            return;
        }
    };
    let span = info_span!("client", connection = id, addr = %connection.remote_address());
    async {
        info!("connected");
        let (events_tx, events_rx) = tokio::sync::mpsc::channel(EVENT_QUEUE_CAPACITY);
        let _ = updates.send(Update::Connected {
            id,
            connection: connection.clone(),
            events: events_tx,
        });
        tokio::spawn(write_events(connection.clone(), events_rx));
        let error = loop {
            tokio::select! {
                stream = connection.accept_bi() => match stream {
                    Ok((send, recv)) => {
                        tokio::spawn(answer(id, send, recv, updates.clone()));
                    }
                    Err(e) => break e,
                },
                stream = connection.accept_uni() => match stream {
                    Ok(recv) => {
                        tokio::spawn(read_input(id, recv, updates.clone()));
                    }
                    Err(e) => break e,
                },
            }
        };
        warn!(%error, "lost the connection");
        let _ = updates.send(Update::Lost { id });
    }
    .instrument(span)
    .await;
}

/// Reads a control call, passes it to the server thread, and writes its reply.
async fn answer(
    id: ConnectionId,
    mut send: SendStream,
    mut recv: RecvStream,
    updates: UnboundedSender<Update>,
) {
    let answered = async {
        let request = recv
            .read_to_end(net::MAX_CONTROL_SIZE)
            .await
            .map_err(|e| net::error("failed to read a call", e))?;
        let (reply_tx, reply_rx) = oneshot::channel();
        updates
            .send(Update::Call {
                id,
                request,
                reply: reply_tx,
            })
            .map_err(|e| net::error("the server stopped", e))?;
        let reply = reply_rx
            .await
            .map_err(|e| net::error("the server did not answer", e))?;
        send.write_all(&reply)
            .await
            .map_err(|e| net::error("failed to send a reply", e))?;
        send.finish()
            .map_err(|e| net::error("failed to send a reply", e))
    };
    if let Err(e) = answered.await {
        debug!(error = %e, "failed to answer a call");
    }
}

/// Reads an input, or a skip notice, and passes it to the server thread.
async fn read_input(id: ConnectionId, mut recv: RecvStream, updates: UnboundedSender<Update>) {
    match recv.read_to_end(net::MAX_INPUT_SIZE).await {
        Ok(message) => {
            let _ = updates.send(Update::Input { id, message });
        }
        // The input's crossing times out on the client.
        Err(e) => debug!(error = %e, "failed to read an input"),
    }
}

/// Writes the client's events on one stream, in order, until the connection or the server stops.
async fn write_events(connection: Connection, mut events: tokio::sync::mpsc::Receiver<Bytes>) {
    let written = async {
        let stream = connection
            .open_uni()
            .await
            .map_err(|e| net::error("failed to open the event stream", e))?;
        let mut frames = FramedWrite::new(stream, net::event_codec());
        while let Some(frame) = events.recv().await {
            frames
                .send(frame)
                .await
                .map_err(|e| net::error("failed to send an event", e))?;
        }
        Ok::<(), Error>(())
    };
    if let Err(e) = written.await {
        debug!(error = %e, "stopped sending events");
        connection.close(net::BROKEN, b"event stream broken");
    }
}
