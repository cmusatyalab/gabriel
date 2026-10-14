//! The publishing end of an event service: the server for its engines, and the client for its
//! producers and for the remote engines it relays ("Event services and subscriptions" in the
//! design note). [`Subscription`](crate::Subscription) is the reading end.

use iceoryx2::{
    port::{notifier::Notifier, publisher::Publisher, publisher::PublisherCreateError},
    prelude::*,
    service::port_factory::{event, publish_subscribe},
};

use crate::{
    Error, Result,
    ipc::types::EventRecord,
    services::{self, EventPayload},
};

/// The slice length a publisher starts with. It grows as needed.
const INITIAL_SLICE_LEN: usize = 4096;

/// The one publisher of an event service. It numbers the events, and wakes the subscribers after
/// each batch.
#[derive(Debug)]
pub(crate) struct EventPublisher {
    events: Publisher<ipc::Service, EventPayload, EventRecord>,
    ready: Notifier<ipc::Service>,
    /// The sequence number of the next event.
    next_seq: u64,
    /// Whether events were published since the subscribers were last woken.
    unannounced: bool,
}

impl EventPublisher {
    /// Publishes the events of the engine `name`. Fails if another process publishes them, e.g.
    /// the server of a local engine with the same name as a remote one.
    pub(crate) fn engine(node: &Node<ipc::Service>, name: &str) -> Result<Self> {
        Self::open(
            &services::open_engine_events(node, name)?,
            &services::open_engine_events_ready(node, name)?,
        )
        .map_err(|e| match e {
            Error::InvalidArgument(_) => Error::InvalidArgument(format!(
                "the events of engine {name:?} are already published on this machine"
            )),
            e => e,
        })
    }

    /// Publishes the events about the inputs of the producer `name`, for its app.
    pub(crate) fn producer(node: &Node<ipc::Service>, name: &str) -> Result<Self> {
        Self::open(
            &services::open_producer_results(node, name)?,
            &services::open_producer_results_ready(node, name)?,
        )
    }

    fn open(
        events: &publish_subscribe::PortFactory<ipc::Service, EventPayload, EventRecord>,
        ready: &event::PortFactory<ipc::Service>,
    ) -> Result<Self> {
        let events = events
            .publisher_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .allocation_strategy(AllocationStrategy::PowerOfTwo)
            .create()
            .map_err(|e| match e {
                PublisherCreateError::ExceedsMaxSupportedPublishers => Error::InvalidArgument(
                    String::from("the event service already has a publisher"),
                ),
                e => Error::Internal(format!("failed to create event publisher: {e}")),
            })?;
        let ready = ready
            .notifier_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create event notifier: {e}")))?;
        Ok(Self {
            events,
            ready,
            next_seq: 1,
            unannounced: false,
        })
    }

    /// Numbers `record` and publishes it, with `result` as the payload.
    pub(crate) fn publish(&mut self, record: EventRecord, result: &[u8]) -> Result<()> {
        let mut sample = self
            .events
            .loan_slice_uninit(result.len())
            .map_err(|e| Error::Internal(format!("failed to loan event: {e}")))?;
        *sample.user_header_mut() = record.numbered(self.next_seq);
        self.next_seq += 1;
        sample
            .write_from_slice(result)
            .send()
            .map_err(|e| Error::Internal(format!("failed to publish event: {e}")))?;
        self.unannounced = true;
        Ok(())
    }

    /// Wakes the subscribers if events were published since they were last woken.
    pub(crate) fn wake_subscribers(&mut self) -> Result<()> {
        if !std::mem::take(&mut self.unannounced) {
            return Ok(());
        }
        self.ready
            .notify()
            .map_err(|e| Error::Internal(format!("failed to wake event subscribers: {e}")))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_util::unique;

    #[test]
    fn an_engines_events_have_one_publisher() {
        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let name = unique("detector");
        let _first = EventPublisher::engine(&node, &name).unwrap();

        let second = EventPublisher::engine(&node, &name);

        assert!(matches!(second, Err(Error::InvalidArgument(message)) if message.contains(&name)));
    }
}
