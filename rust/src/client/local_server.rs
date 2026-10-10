//! The client's ends of its local server's services: control calls, inputs sent on crossings, and
//! the stream of events about the client's flows. It also tells when the connection is lost: the
//! server's process stopped, or the stream lost events.

use std::{
    thread,
    time::{Duration, Instant},
};

use iceoryx2::identifiers::UniqueNodeId;
use iceoryx2::node::NodeView;
use iceoryx2::port::{
    client::Client as ControlClient, listener::Listener, notifier::Notifier, publisher::Publisher,
    subscriber::Subscriber,
};
use iceoryx2::prelude::*;

use crate::{
    Error, Result,
    ipc::types::{CrossingHeader, CrossingKind, EventGaps, EventRecord, PayloadType},
    naming,
    routing::{
        ids::{InputId, OfferId},
        server_router::InstallFlow,
    },
    services::{self, ClientEventPayload, ControlPayload, CrossingPayload},
    wire::{self, Call, Reply, Request, Response},
};

/// How long a control call waits for the server's reply. The server answers calls every time it
/// wakes, so this only runs out if the server stopped.
const CONTROL_TIMEOUT: Duration = Duration::from_secs(1);

/// How often a control call checks for the reply. iceoryx2 signals no event for a reply, and calls
/// are rare, so polling costs little.
const CONTROL_POLL_INTERVAL: Duration = Duration::from_millis(1);

/// The slice length the input publisher and the control client start with. They grow as needed.
const INITIAL_SLICE_LEN: usize = 4096;

/// An event from the server, with its result bytes, if any.
#[derive(Debug)]
pub(super) struct ServerEvent {
    pub(super) record: EventRecord,
    pub(super) result: Box<[u8]>,
}

pub(super) struct LocalServer {
    /// The client's name, which the server sends the flow's events to.
    client: String,
    /// The server's name.
    name: String,
    control: ControlClient<ipc::Service, ControlPayload, (), ControlPayload, ()>,
    inputs: Publisher<ipc::Service, CrossingPayload, CrossingHeader>,
    /// Wakes the server after each input.
    input_ready: Notifier<ipc::Service>,
    events: Subscriber<ipc::Service, ClientEventPayload, EventRecord>,
    /// Wakes the client after the server sends events.
    events_ready: Listener<ipc::Service>,
    /// Finds events the stream lost.
    gaps: EventGaps,
    /// The node of the server's running process, as of the last check.
    instance: Option<UniqueNodeId>,
}

impl LocalServer {
    /// Connects the client `client` to the local server `name`. The server may start later.
    pub(super) fn open(node: &Node<ipc::Service>, client: &str, name: &str) -> Result<Self> {
        let control = services::open_server_control(node, name)?
            .client_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .allocation_strategy(AllocationStrategy::PowerOfTwo)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create control client: {e}")))?;
        let inputs = services::open_server_input(node, name)?
            .publisher_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .allocation_strategy(AllocationStrategy::PowerOfTwo)
            .set_backpressure_handler(services::fail_when_full)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create input publisher: {e}")))?;
        let input_ready = services::open_server_input_ready(node, name)?
            .notifier_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create input notifier: {e}")))?;
        let events = services::open_client_events(node, client)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create event subscriber: {e}")))?;
        let events_ready = services::open_client_events_ready(node, client)?
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create event listener: {e}")))?;
        Ok(Self {
            client: client.to_owned(),
            name: name.to_owned(),
            control,
            inputs,
            input_ready,
            events,
            events_ready,
            gaps: EventGaps::default(),
            instance: None,
        })
    }

    /// Installs the server's part of a flow, and returns the flow's session on the server.
    pub(super) fn install(&self, spec: InstallFlow) -> Result<u64> {
        let call = Call::Install {
            client: self.client.clone(),
            spec,
        };
        match self.call(call)? {
            Reply::Installed { session } => Ok(session),
            reply => Err(self.unexpected(&reply)),
        }
    }

    /// Removes the flow `session` from the server.
    pub(super) fn remove_flow(&self, session: u64) -> Result<()> {
        let call = Call::RemoveFlow {
            client: self.client.clone(),
            session,
        };
        match self.call(call)? {
            Reply::Removed => Ok(()),
            reply => Err(self.unexpected(&reply)),
        }
    }

    /// Sends the input `id`, whose bytes are `data`, on a crossing of the flow `flow`, as the offer
    /// `offer`, and wakes the server.
    pub(super) fn send(
        &self,
        flow: u64,
        offer: OfferId,
        id: InputId,
        payload_type: PayloadType,
        data: &[u8],
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

    /// Tells the server that the offer `offer` of the input `id`, on a crossing of the flow `flow`,
    /// was skipped and will never be sent, so that the server can tell the subscribers of the
    /// crossing's target engines.
    pub(super) fn send_skipped(&self, flow: u64, offer: OfferId, id: InputId) -> Result<()> {
        let header = CrossingHeader {
            kind: CrossingKind::Skipped,
            flow,
            offer,
            id,
            payload_type: PayloadType::new(),
        };
        self.send_on_crossing(header, &[])
    }

    fn send_on_crossing(&self, header: CrossingHeader, data: &[u8]) -> Result<()> {
        let mut sample = self
            .inputs
            .loan_slice_uninit(data.len())
            .map_err(|e| Error::Internal(format!("failed to loan input: {e}")))?;
        *sample.user_header_mut() = header;
        let receivers = sample
            .write_from_slice(data)
            .send()
            .map_err(|e| Error::Internal(format!("failed to send input: {e}")))?;
        if receivers == 0 {
            return Err(self.not_running());
        }
        self.input_ready
            .notify()
            .map_err(|e| Error::Internal(format!("failed to wake the server: {e}")))?;
        Ok(())
    }

    /// The listener that wakes the client when the server sends events.
    pub(super) fn events_ready(&self) -> &Listener<ipc::Service> {
        &self.events_ready
    }

    /// Returns the events the server sent since the last call, in order, and how many events the
    /// stream lost, because the client fell too far behind.
    pub(super) fn take_events(&mut self) -> Result<(Vec<ServerEvent>, u64)> {
        // Without this the waitset keeps reporting the same notifications.
        self.events_ready
            .try_wait(|_| {})
            .map_err(|e| Error::Internal(format!("failed to drain event wake-ups: {e}")))?;
        let mut events = Vec::new();
        let mut lost = 0;
        while let Some(sample) = self
            .events
            .receive()
            .map_err(|e| Error::Internal(format!("failed to receive event: {e}")))?
        {
            let record = sample.user_header().clone();
            lost += self.gaps.observe(record.seq);
            events.push(ServerEvent {
                record,
                result: Box::from(sample.payload()),
            });
        }
        Ok((events, lost))
    }

    /// Looks for the server's process, by its node, among those `node` can see. Returns whether the process seen at the last
    /// check is gone: the server stopped, or stopped and started again.
    pub(super) fn check(&mut self, node: &Node<ipc::Service>) -> Result<bool> {
        let mut running = None;
        Node::<ipc::Service>::list(node.config(), |state| {
            if let NodeState::Alive(node) = state
                && let Some(details) = node.details().as_ref()
                && naming::server_of_node(details.name().as_str()) == Some(self.name.as_str())
            {
                running = Some(*node.id());
                return CallbackProgression::Stop;
            }
            CallbackProgression::Continue
        })
        .map_err(|e| Error::Internal(format!("failed to list nodes: {e:?}")))?;
        let lost = self.instance.is_some() && running != self.instance;
        self.instance = running;
        Ok(lost)
    }

    /// Whether the server's process was running at the last check.
    pub(super) fn is_running(&self) -> bool {
        self.instance.is_some()
    }

    /// Sends `call` to the server, and waits for its reply.
    fn call(&self, call: Call) -> Result<Reply> {
        let request = wire::encode(&Request::new(call))?;
        let pending = self
            .control
            .loan_slice_uninit(request.len())
            .map_err(|e| Error::Internal(format!("failed to loan request: {e}")))?
            .write_from_slice(&request)
            .send()
            .map_err(|e| Error::Internal(format!("failed to send request: {e}")))?;
        if pending.number_of_server_connections() == 0 {
            return Err(self.not_running());
        }
        let deadline = Instant::now() + CONTROL_TIMEOUT;
        loop {
            let response = pending
                .receive()
                .map_err(|e| Error::Internal(format!("failed to receive reply: {e}")))?;
            if let Some(response) = response {
                return wire::decode::<Response>(response.payload())?;
            }
            if Instant::now() >= deadline {
                return Err(Error::Internal(format!(
                    "local server {:?} did not reply within {CONTROL_TIMEOUT:?}",
                    self.name
                )));
            }
            thread::sleep(CONTROL_POLL_INTERVAL);
        }
    }

    fn not_running(&self) -> Error {
        Error::Internal(format!("local server {:?} is not running", self.name))
    }

    fn unexpected(&self, reply: &Reply) -> Error {
        Error::Internal(format!(
            "local server {:?} sent an unexpected reply: {reply:?}",
            self.name
        ))
    }
}
