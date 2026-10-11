//! How the server thread reaches its clients: control calls and inputs from them, and events to
//! them. Clients on the server's machine are reached over iceoryx2
//! ([`LocalClients`](super::local_clients::LocalClients)); remote clients will be reached over
//! QUIC, through the same trait.

use std::time::Instant;

use iceoryx2::{port::listener::Listener, prelude::*};

use crate::{
    Result,
    server::dispatch::{ClientEvent, Dispatcher},
};

/// The server's connections to one kind of client.
pub(crate) trait ClientLink {
    /// The listener that wakes the server thread when a client sent a call or an input.
    fn ready(&self) -> &Listener<ipc::Service>;

    /// Answers every waiting control call, then passes every input the clients sent to the
    /// dispatcher, and every offer they skipped on a crossing. Returns the first error, after
    /// handling the rest. A refused input, e.g. for a flow that was removed, times out on its
    /// crossing at the client.
    fn receive(&mut self, dispatcher: &mut Dispatcher, now: Instant) -> Result<()>;

    /// Whether a client of this link installed the flow `session`.
    fn owns(&self, session: u64) -> bool;

    /// Sends `event` to the client that installed its flow, which this link owns.
    fn send_event(&mut self, event: ClientEvent) -> Result<()>;

    /// Wakes the clients that were sent events since the last call.
    fn flush(&mut self) -> Result<()>;
}

/// Sends every event the dispatcher has for clients over the link that owns its flow, then wakes
/// the clients that got some. Returns the first error, after sending the rest. An event for a flow
/// that was removed is dropped: no client waits for it.
pub(crate) fn send_events(
    dispatcher: &mut Dispatcher,
    links: &mut [Box<dyn ClientLink>],
) -> Result<()> {
    let mut first_error = None;
    while let Some(event) = dispatcher.poll_client_event() {
        let Some(link) = links.iter_mut().find(|link| link.owns(event.session)) else {
            continue;
        };
        if let Err(e) = link.send_event(event) {
            first_error.get_or_insert(e);
        }
    }
    for link in links {
        if let Err(e) = link.flush() {
            first_error.get_or_insert(e);
        }
    }
    first_error.map_or(Ok(()), Err)
}
