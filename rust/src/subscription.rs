//! Subscription: reads the events about a producer's inputs, which the client publishes for the
//! producer's app, or the events of an engine, which its server publishes ("Event services and
//! subscriptions" in the design note). Every event is delivered, in order; a subscriber that falls
//! too far behind loses the oldest, and learns how many as [`Event::Lost`].
//!
//! Each event says when it happened, and for an engine's offer, when the offer was made. With the
//! time the app published the input, these tell how long the input waited, and where.

use std::time::Duration;

use iceoryx2::port::{listener::Listener, subscriber::Subscriber};
use iceoryx2::prelude::*;
use iceoryx2::sample::Sample;
use iceoryx2::service::port_factory::{event, publish_subscribe};
use iceoryx2_bb_container::string::String as _;

use crate::{
    Error, InputId, Result, Timestamp,
    ipc::types::{EngineName, EventGaps, EventKind, EventRecord, Forward},
    services::{self, EventPayload},
};

type EventSample = Sample<ipc::Service, EventPayload, EventRecord>;

/// Reads the events of one event service.
pub struct Subscription {
    #[allow(dead_code)] // keeps the ports alive
    node: Node<ipc::Service>,
    events: Subscriber<ipc::Service, EventPayload, EventRecord>,
    /// Signalled by the publisher after each batch of events.
    ready: Listener<ipc::Service>,
    /// The engine subscribed to; `None` for a producer's results.
    engine: Option<EngineName>,
    gaps: EventGaps,
    /// An event read right after a gap. It is returned after the [`Event::Lost`] that reports the
    /// gap.
    held: Option<EventSample>,
    /// The event returned last, kept so that it stays valid until the next poll.
    current: Option<EventSample>,
}

/// What a subscription read.
#[derive(Debug)]
pub enum Event<'a> {
    /// Events were lost before the next one, because the subscriber fell behind. Inputs whose
    /// events may be among them should not be waited for.
    Lost(Lost<'a>),
    /// An event, as its publisher published it.
    Published(Published<'a>),
}

/// Events a subscription lost.
#[derive(Debug)]
pub struct Lost<'a> {
    count: u64,
    pub(crate) engine: Option<&'a EngineName>,
}

impl Lost<'_> {
    /// How many events were lost.
    pub fn count(&self) -> u64 {
        self.count
    }

    /// The engine whose events were lost, for a subscription to an engine; `None` for a
    /// producer's results.
    pub fn engine(&self) -> Option<&str> {
        self.engine.map(|engine| engine.as_str())
    }
}

/// An event about an input, or about a flow for [`EventKind::Disconnected`]. Valid until the next
/// poll.
#[derive(Debug)]
pub struct Published<'a> {
    pub(crate) record: &'a EventRecord,
    result: &'a [u8],
}

impl Published<'_> {
    pub fn kind(&self) -> EventKind {
        self.record.kind
    }

    /// The input the event is about. Unset for [`EventKind::Disconnected`].
    pub fn id(&self) -> &InputId {
        &self.record.id
    }

    /// The engine the event is about. Empty for [`EventKind::Disconnected`].
    pub fn engine(&self) -> &str {
        self.record.engine.as_str()
    }

    /// The flow the event belongs to. One input can enter several flows.
    pub fn flow(&self) -> &str {
        self.record.flow.as_str()
    }

    /// What the engine forwarded. Only meaningful for [`EventKind::Result`].
    pub fn forward(&self) -> Forward {
        self.record.forward
    }

    /// How to read [`Published::result`]; empty if there is no result.
    pub fn payload_type(&self) -> &str {
        self.record.payload_type.as_str()
    }

    /// The engine's result. Empty if it reported none.
    pub fn result(&self) -> &[u8] {
        self.result
    }

    /// When the event happened, on the clock of the machine whose client or server made it.
    pub fn at(&self) -> Timestamp {
        self.record.at
    }

    /// When the server made the offer the event is about, i.e. when the input reached it or the
    /// engine before passed it on. `None` if the client made the offer, as for a skipped or timed
    /// out crossing, and for [`EventKind::Disconnected`].
    pub fn offered_at(&self) -> Option<Timestamp> {
        self.record.offered_at.as_option_ref().copied()
    }

    /// When the client received the event from the remote server, on this machine's clock. Then
    /// [`at`](Self::at) and [`offered_at`](Self::offered_at) are on the remote server's clock, so
    /// only differences between them mean something here. `None` for events from the local
    /// server or the client.
    pub fn received_at(&self) -> Option<Timestamp> {
        self.record.received_at.as_option_ref().copied()
    }
}

impl Subscription {
    /// Subscribes to the events about the inputs of the producer `name`. Only events published
    /// from now on are read.
    pub fn producer(name: &str) -> Result<Self> {
        let node = create_node()?;
        let events = services::open_producer_results(&node, name)?;
        let ready = services::open_producer_results_ready(&node, name)?;
        Self::open(node, &events, &ready, None)
    }

    /// Subscribes to the events of the engine `name`, which its server publishes. Only events
    /// published from now on are read.
    pub fn engine(name: &str) -> Result<Self> {
        let engine = EngineName::try_from(name)
            .map_err(|_| Error::InvalidArgument(format!("engine name {name:?} is too long")))?;
        let node = create_node()?;
        let events = services::open_engine_events(&node, name)?;
        let ready = services::open_engine_events_ready(&node, name)?;
        Self::open(node, &events, &ready, Some(engine))
    }

    fn open(
        node: Node<ipc::Service>,
        events: &publish_subscribe::PortFactory<ipc::Service, EventPayload, EventRecord>,
        ready: &event::PortFactory<ipc::Service>,
        engine: Option<EngineName>,
    ) -> Result<Self> {
        let events = events
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create subscriber: {e}")))?;
        let ready = ready
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create listener: {e}")))?;
        Ok(Self {
            node,
            events,
            ready,
            engine,
            gaps: EventGaps::default(),
            held: None,
            current: None,
        })
    }

    /// Waits until an event is ready, or `timeout` passes, and returns [`Error::Again`] then. The
    /// event itself comes from [`Subscription::poll`].
    pub fn wait(&self, timeout: Duration) -> Result<()> {
        self.drain_ready()?;
        if self.has_event()? {
            return Ok(());
        }
        // The publisher signals after it publishes, so an event that arrived since the check
        // above ends this wait at once.
        let woken = services::wait_for_notification(&self.ready, timeout)
            .map_err(|e| Error::Internal(format!("failed to wait for an event: {e}")))?;
        if woken || self.has_event()? {
            Ok(())
        } else {
            Err(Error::Again)
        }
    }

    /// The listener the publisher signals after each batch of events.
    pub(crate) fn ready(&self) -> &Listener<ipc::Service> {
        &self.ready
    }

    /// Consumes the notifications for events that are already here, which would wake the next
    /// wait for nothing.
    pub(crate) fn drain_ready(&self) -> Result<()> {
        self.ready
            .try_wait(|_| {})
            .map_err(|e| Error::Internal(format!("failed to wait for an event: {e}")))?;
        Ok(())
    }

    /// Whether [`Subscription::poll`] has something to return.
    pub(crate) fn has_event(&self) -> Result<bool> {
        Ok(self.held.is_some()
            || self
                .events
                .has_samples()
                .map_err(|e| Error::Internal(format!("failed to check for an event: {e}")))?)
    }

    /// Returns the next event, or [`Error::Again`] if none is available. The event stays valid
    /// until the next poll.
    pub fn poll(&mut self) -> Result<Event<'_>> {
        self.current = None;
        let sample = match self.held.take() {
            Some(sample) => sample,
            None => {
                let sample = self
                    .events
                    .receive()
                    .map_err(|e| Error::Internal(format!("failed to receive an event: {e}")))?
                    .ok_or(Error::Again)?;
                let lost = self.gaps.observe(sample.user_header().seq);
                if lost > 0 {
                    self.held = Some(sample);
                    return Ok(Event::Lost(Lost {
                        count: lost,
                        engine: self.engine.as_ref(),
                    }));
                }
                sample
            }
        };
        let sample = self.current.insert(sample);
        Ok(Event::Published(Published {
            record: sample.user_header(),
            result: sample.payload(),
        }))
    }
}

fn create_node() -> Result<Node<ipc::Service>> {
    NodeBuilder::new()
        .create::<ipc::Service>()
        .map_err(|e| Error::Internal(format!("failed to create node: {e}")))
}

impl std::fmt::Debug for Subscription {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Subscription")
            .field("held", &self.held.is_some())
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
mod tests {
    use iceoryx2::port::{notifier::Notifier, publisher::Publisher};

    use super::*;
    use crate::{
        ipc::types::{EngineName, FlowName},
        routing::ids::{EdgeId, OfferId, ProducerName},
        test_util::unique,
    };

    const RESULT: [u8; 2] = [9, 8];
    /// Long enough for a notification within one process, short enough to keep tests fast.
    const WAIT_TIMEOUT: Duration = Duration::from_millis(100);

    /// A subscription, and the publisher's end of its service.
    struct Fixture {
        subscription: Subscription,
        events: Publisher<ipc::Service, EventPayload, EventRecord>,
        ready: Notifier<ipc::Service>,
    }

    impl Fixture {
        /// A subscription to a producer's results.
        fn new() -> Self {
            let name = unique("camera");
            let subscription = Subscription::producer(&name).unwrap();
            let events = services::open_producer_results(&subscription.node, &name).unwrap();
            let ready = services::open_producer_results_ready(&subscription.node, &name).unwrap();
            Self::publishing(subscription, &events, &ready)
        }

        /// A subscription to the engine `name`'s events.
        fn engine(name: &str) -> Self {
            let subscription = Subscription::engine(name).unwrap();
            let events = services::open_engine_events(&subscription.node, name).unwrap();
            let ready = services::open_engine_events_ready(&subscription.node, name).unwrap();
            Self::publishing(subscription, &events, &ready)
        }

        fn publishing(
            subscription: Subscription,
            events: &publish_subscribe::PortFactory<ipc::Service, EventPayload, EventRecord>,
            ready: &event::PortFactory<ipc::Service>,
        ) -> Self {
            let events = events
                .publisher_builder()
                .initial_max_slice_len(RESULT.len())
                .create()
                .unwrap();
            let ready = ready.notifier_builder().create().unwrap();
            Self {
                subscription,
                events,
                ready,
            }
        }

        /// Publishes event number `seq`, a result for the input `seq`, and signals it.
        fn publish(&self, seq: u64) {
            let id = InputId {
                producer: ProducerName::try_from("camera").unwrap(),
                session: 1,
                seq,
            };
            let offer = OfferId {
                session: 1,
                edge: EdgeId(1),
                seq,
            };
            let record = EventRecord::result(
                offer,
                id,
                EngineName::try_from("detector").unwrap(),
                FlowName::try_from("tracking").unwrap(),
                Forward::Stop,
                &[],
                Default::default(),
            )
            .unwrap()
            .numbered(seq);
            let mut sample = self.events.loan_slice_uninit(RESULT.len()).unwrap();
            *sample.user_header_mut() = record;
            sample.write_from_slice(&RESULT).send().unwrap();
            self.ready.notify().unwrap();
        }
    }

    /// The input sequence number of a published event, or `None` for `Lost`.
    fn seq(event: Event<'_>) -> Option<u64> {
        match event {
            Event::Published(published) => Some(published.id().seq()),
            Event::Lost(_) => None,
        }
    }

    #[test]
    fn published_event_is_read_with_its_result() {
        let mut fixture = Fixture::new();
        fixture.publish(1);

        fixture.subscription.wait(WAIT_TIMEOUT).unwrap();
        let Event::Published(event) = fixture.subscription.poll().unwrap() else {
            panic!("expected an event");
        };

        assert_eq!(event.kind(), EventKind::Result);
        assert_eq!(event.engine(), "detector");
        assert_eq!(event.flow(), "tracking");
        assert_eq!(event.result(), RESULT);
        assert!(event.at() > Timestamp::default());
    }

    #[test]
    fn wait_with_no_time_left_returns_at_once() {
        let fixture = Fixture::new();

        assert!(matches!(
            fixture.subscription.wait(Duration::ZERO),
            Err(Error::Again)
        ));
    }

    #[test]
    fn wait_without_an_event_times_out() {
        let fixture = Fixture::new();

        assert!(matches!(
            fixture.subscription.wait(WAIT_TIMEOUT),
            Err(Error::Again)
        ));
    }

    #[test]
    fn poll_without_an_event_is_again() {
        let mut fixture = Fixture::new();

        assert!(matches!(fixture.subscription.poll(), Err(Error::Again)));
    }

    #[test]
    fn gap_is_reported_as_lost_before_the_next_event() {
        let mut fixture = Fixture::new();
        fixture.publish(1);
        fixture.publish(4);

        assert_eq!(seq(fixture.subscription.poll().unwrap()), Some(1));
        let Event::Lost(lost) = fixture.subscription.poll().unwrap() else {
            panic!("expected lost events");
        };
        assert_eq!((lost.count(), lost.engine()), (2, None));
        // The event after the gap is still waiting, so a wait returns at once.
        fixture.subscription.wait(WAIT_TIMEOUT).unwrap();
        assert_eq!(seq(fixture.subscription.poll().unwrap()), Some(4));
    }

    #[test]
    fn lost_events_of_an_engine_name_it() {
        let detector = unique("detector");
        let mut fixture = Fixture::engine(&detector);
        fixture.publish(1);
        fixture.publish(3);
        fixture.subscription.poll().unwrap();

        let Event::Lost(lost) = fixture.subscription.poll().unwrap() else {
            panic!("expected lost events");
        };

        assert_eq!((lost.count(), lost.engine()), (1, Some(detector.as_str())));
    }
}
