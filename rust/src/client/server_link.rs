//! How the client thread reaches a server: control calls, inputs sent on crossings, skip notices,
//! and the stream of events about the client's flows. The local server is reached over iceoryx2
//! ([`LocalServer`](super::local_server::LocalServer)); the remote server will be reached over
//! QUIC, through the same trait.

use std::sync::Arc;

use iceoryx2::{port::listener::Listener, prelude::*};

use crate::{
    Result,
    ipc::types::{EventRecord, PayloadType},
    routing::{
        ids::{InputId, OfferId},
        server_router::InstallFlow,
    },
};

/// An event from a server, with its result bytes, if any.
#[derive(Debug)]
pub(super) struct ServerEvent {
    pub(super) record: EventRecord,
    pub(super) result: Box<[u8]>,
}

/// What a server sent since the client last looked.
#[derive(Debug, Default)]
pub(super) struct Received {
    /// The events, in the order the server sent them.
    pub(super) events: Vec<ServerEvent>,
    /// Whether the connection was lost on the way, e.g. because the stream lost events. The client
    /// can then no longer tell which of the server's offers are open.
    pub(super) lost: bool,
}

/// The client's connection to one server.
pub(super) trait ServerLink {
    /// Installs the server's part of a flow, and returns the flow's session on the server.
    fn install(&self, spec: InstallFlow) -> Result<u64>;

    /// Removes the flow `session` from the server.
    fn remove_flow(&self, session: u64) -> Result<()>;

    /// Sends the input `id`, whose bytes are `data`, on a crossing of the flow `flow`, as the offer
    /// `offer`.
    fn send(
        &self,
        flow: u64,
        offer: OfferId,
        id: InputId,
        payload_type: PayloadType,
        data: Arc<[u8]>,
    ) -> Result<()>;

    /// Tells the server that the offer `offer` of the input `id`, on a crossing of the flow `flow`,
    /// was skipped and will never be sent, so that the server can tell the subscribers of the
    /// crossing's target engines.
    fn send_skipped(&self, flow: u64, offer: OfferId, id: InputId) -> Result<()>;

    /// The listener that wakes the client thread when the server sent something.
    fn ready(&self) -> &Listener<ipc::Service>;

    /// Returns what the server sent since the last call.
    fn take_events(&mut self) -> Result<Received>;

    /// Checks the connection. Returns whether it was lost since the last check.
    fn check(&mut self) -> Result<bool>;

    /// Whether the server could be reached at the last check.
    fn is_connected(&self) -> bool;
}
