//! The client publishes the remote engines' events on the device, so that device processes can
//! subscribe to remote engines as they do to local ones ("Event services and subscriptions" in
//! the design note). The client is the one publisher of each relayed engine's event service.
//!
//! An engine's service is opened at its first event. If it already has a publisher then, the
//! engine is served on this machine too, e.g. because the remote server runs here, and the client
//! does not relay it.

use std::collections::HashMap;

use iceoryx2::prelude::*;
use iceoryx2_bb_container::string::String as _;
use tracing::info;

use crate::{
    Error, Result,
    events::EventPublisher,
    ipc::types::{EngineName, EventRecord},
};

/// The event services of the remote engines that the client's flows use.
#[derive(Debug, Default)]
pub(super) struct Relays {
    engines: HashMap<EngineName, Relay>,
}

#[derive(Debug)]
struct Relay {
    state: RelayState,
    /// The flows that use the engine. The service is closed when none do.
    flows: usize,
}

#[derive(Debug)]
enum RelayState {
    /// No event was relayed yet.
    Unopened,
    Open(Box<EventPublisher>),
    /// Another publisher on this machine publishes the engine's events.
    ServedHere,
}

impl Relays {
    /// Relays the events of `engines` for one more flow.
    pub(super) fn add(&mut self, engines: impl IntoIterator<Item = EngineName>) {
        for engine in engines {
            self.engines
                .entry(engine)
                .or_insert(Relay {
                    state: RelayState::Unopened,
                    flows: 0,
                })
                .flows += 1;
        }
    }

    /// Stops relaying `engines` for one flow. An engine no flow uses any more is closed.
    pub(super) fn remove(&mut self, engines: impl IntoIterator<Item = EngineName>) {
        for engine in engines {
            if let Some(relay) = self.engines.get_mut(&engine) {
                relay.flows -= 1;
                if relay.flows == 0 {
                    self.engines.remove(&engine);
                }
            }
        }
    }

    /// Publishes `record` on its engine's service, if the engine is relayed. The first event of
    /// an engine opens its service.
    pub(super) fn publish(
        &mut self,
        node: &Node<ipc::Service>,
        record: &EventRecord,
        result: &[u8],
    ) -> Result<()> {
        let Some(relay) = self.engines.get_mut(&record.engine) else {
            return Ok(());
        };
        if let RelayState::Unopened = relay.state {
            relay.state = match EventPublisher::engine(node, record.engine.as_str()) {
                Ok(events) => RelayState::Open(Box::new(events)),
                Err(e @ Error::InvalidArgument(_)) => {
                    info!(engine = record.engine.as_str(), error = %e, "not relaying a remote engine");
                    RelayState::ServedHere
                }
                // Tried again at the next event.
                Err(e) => return Err(e),
            };
        }
        match &mut relay.state {
            RelayState::Open(events) => events.publish(record.clone(), result),
            RelayState::Unopened | RelayState::ServedHere => Ok(()),
        }
    }

    /// Publishes `record`, a derived result of a remote engine, on the engine's service, whether
    /// or not a flow uses the engine: an engine that only derives results, such as fusion, is in
    /// no flow. An engine no flow uses stays relayed until the client stops.
    pub(super) fn publish_derived(
        &mut self,
        node: &Node<ipc::Service>,
        record: &EventRecord,
        result: &[u8],
    ) -> Result<()> {
        self.engines.entry(record.engine).or_insert(Relay {
            state: RelayState::Unopened,
            flows: 0,
        });
        self.publish(node, record, result)
    }

    /// Wakes the subscribers of every engine with events published since the last call. Returns
    /// the first error, after waking the rest.
    pub(super) fn wake_subscribers(&mut self) -> Result<()> {
        let mut first_error = None;
        for relay in self.engines.values_mut() {
            if let RelayState::Open(events) = &mut relay.state
                && let Err(e) = events.wake_subscribers()
            {
                first_error.get_or_insert(e);
            }
        }
        first_error.map_or(Ok(()), Err)
    }
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;
    use crate::{
        Subscription,
        ipc::types::FlowName,
        routing::ids::{EdgeId, InputId, OfferId},
        subscription::Event,
        test_util::unique,
    };

    /// Far longer than a wake-up within one process takes.
    const WAIT: Duration = Duration::from_secs(5);

    fn node() -> Node<ipc::Service> {
        NodeBuilder::new().create::<ipc::Service>().unwrap()
    }

    fn engine(base: &str) -> EngineName {
        EngineName::try_from(unique(base).as_str()).unwrap()
    }

    fn sent(engine: EngineName) -> EventRecord {
        EventRecord::sent(
            OfferId {
                session: 1,
                edge: EdgeId(1),
                seq: 1,
            },
            InputId::default(),
            engine,
            FlowName::try_from("tracking").unwrap(),
        )
    }

    #[test]
    fn subscriber_gets_a_relayed_event() {
        let node = node();
        let detector = engine("detector");
        let mut subscription = Subscription::engine(detector.as_str()).unwrap();
        let mut relays = Relays::default();
        relays.add([detector]);

        relays.publish(&node, &sent(detector), &[]).unwrap();
        relays.wake_subscribers().unwrap();

        subscription.wait(WAIT).unwrap();
        let Event::Published(event) = subscription.poll().unwrap() else {
            panic!("expected an event");
        };
        assert_eq!(event.engine(), detector.as_str());
    }

    #[test]
    fn an_engine_served_on_this_machine_is_not_relayed() {
        let node = node();
        let detector = engine("detector");
        let _server = EventPublisher::engine(&node, detector.as_str()).unwrap();
        let mut relays = Relays::default();
        relays.add([detector]);

        relays.publish(&node, &sent(detector), &[]).unwrap();

        assert!(matches!(
            relays.engines[&detector].state,
            RelayState::ServedHere
        ));
    }

    #[test]
    fn an_engine_two_flows_use_stays_relayed_until_both_are_removed() {
        let node = node();
        let detector = engine("detector");
        let mut relays = Relays::default();
        relays.add([detector]);
        relays.add([detector]);
        relays.publish(&node, &sent(detector), &[]).unwrap();

        relays.remove([detector]);
        assert!(relays.engines.contains_key(&detector));
        relays.remove([detector]);
        assert!(relays.engines.is_empty());
        // Its service was closed, so it can be published again.
        EventPublisher::engine(&node, detector.as_str()).unwrap();
    }

    #[test]
    fn a_derived_result_of_an_engine_in_no_flow_is_relayed() {
        let node = node();
        let fusion = engine("fusion");
        let mut subscription = Subscription::engine(fusion.as_str()).unwrap();
        let mut relays = Relays::default();
        let record = EventRecord::derived(
            InputId::default(),
            fusion,
            FlowName::try_from("tracking").unwrap(),
            Default::default(),
        );

        relays.publish_derived(&node, &record, b"person").unwrap();
        relays.wake_subscribers().unwrap();

        subscription.wait(WAIT).unwrap();
        let Event::Published(event) = subscription.poll().unwrap() else {
            panic!("expected an event");
        };
        assert_eq!(event.result(), b"person");
    }

    #[test]
    fn an_engine_no_flow_relays_is_not_published() {
        let node = node();
        let mut relays = Relays::default();

        relays
            .publish(&node, &sent(engine("detector")), &[])
            .unwrap();

        assert!(relays.engines.is_empty());
    }
}
