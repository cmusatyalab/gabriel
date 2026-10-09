//! iceoryx2 services that connect Gabriel's local components. Every side of a service opens it
//! through here, so they always agree on its types.

use iceoryx2::prelude::*;
use iceoryx2::service::port_factory::{event, publish_subscribe};

use crate::envelope::Envelope;
use crate::ipc::types::{EventRecord, InputHeader, OutcomeRecord};
use crate::{Error, Result, naming};

/// Payload of a producer's input service.
pub(crate) type InputPayload = [u8];

/// Payload of an engine's input service: the input's bytes, which its payload type explains.
pub(crate) type EngineInputPayload = [u8];

/// Inputs an engine's input service can hold. The server delivers at most `ENGINE_DELIVERY_LIMIT`
/// inputs without an outcome, and holds the others itself, so this bound does not grow with the
/// number of edges, flows or clients that feed the engine, and the service never has to be resized
/// while it runs. It is larger than the delivery limit only as a safety margin.
pub(crate) const ENGINE_INPUT_BUFFER_SIZE: usize = 4;

/// Inputs the server gives an engine before their outcomes arrive: one to work on, and one ready
/// for when it finishes. Newer inputs wait at the server, where a newer one replaces an older one,
/// so the engine never works on a stale input.
pub(crate) const ENGINE_DELIVERY_LIMIT: usize = 2;
const _: () = assert!(ENGINE_DELIVERY_LIMIT <= ENGINE_INPUT_BUFFER_SIZE);

/// Payload of an engine's event service: the event's result bytes, if it has any.
pub(crate) type EventPayload = [u8];

/// Publishers of an engine's event service: the server for its own engines, or the client for
/// engines it relays from a remote server. Never both, so events have one order.
const EVENT_PUBLISHERS: usize = 1;
/// Subscribers of an engine's event service: the server, the client, and the engines that
/// subscribe to it.
const EVENT_SUBSCRIBERS: usize = 16;
/// Events a subscriber can fall behind by. Past this the oldest are dropped, and the subscriber
/// reports `Lost`.
const EVENT_BUFFER_SIZE: usize = 256;

/// Payload of an engine's outcome service: the engine's new output, then the result bytes.
pub(crate) type OutcomePayload = [u8];

/// Outcomes an engine can have waiting for its server. An engine reports one outcome per
/// delivered input, and the server delivers at most `ENGINE_DELIVERY_LIMIT` inputs without an
/// outcome, so the buffer never fills. Outcomes are never dropped.
const OUTCOME_BUFFER_SIZE: usize = ENGINE_INPUT_BUFFER_SIZE;

/// Opens (or creates) the input service of the producer `name`.
pub(crate) fn open_producer_input(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, InputPayload, Envelope>> {
    node.service_builder(&naming::producer_input_service(name)?)
        .publish_subscribe::<InputPayload>()
        .user_header::<Envelope>()
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open input of producer {name:?}: {e}")))
}

/// Opens (or creates) the event the producer `name` signals after each input.
pub(crate) fn open_producer_ready(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    node.service_builder(&naming::producer_ready_service(name)?)
        .event()
        .open_or_create()
        .map_err(|e| {
            Error::Internal(format!(
                "failed to open ready event of producer {name:?}: {e}"
            ))
        })
}

/// Opens (or creates) the input service of the engine `name`. Each sample is one offered input:
/// the header says which offer and input it is, and the payload holds its bytes. It has one
/// publisher, the server, and one subscriber, the engine. A delivered input is never dropped,
/// because its offer would then never end.
pub(crate) fn open_engine_input(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, EngineInputPayload, InputHeader>> {
    node.service_builder(&naming::engine_service(name)?)
        .publish_subscribe::<EngineInputPayload>()
        .user_header::<InputHeader>()
        .max_publishers(1)
        .max_subscribers(1)
        .subscriber_max_buffer_size(ENGINE_INPUT_BUFFER_SIZE)
        .enable_safe_overflow(false)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open input of engine {name:?}: {e}")))
}

/// Opens (or creates) the service the engine `name` reports its outcomes on, to its server. It
/// has one publisher, the engine, and one subscriber, the server.
pub(crate) fn open_engine_outcome(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, OutcomePayload, OutcomeRecord>> {
    node.service_builder(&naming::engine_outcome_service(name)?)
        .publish_subscribe::<OutcomePayload>()
        .user_header::<OutcomeRecord>()
        .max_publishers(1)
        .max_subscribers(1)
        .subscriber_max_buffer_size(OUTCOME_BUFFER_SIZE)
        .enable_safe_overflow(false)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open outcome of engine {name:?}: {e}")))
}

/// Opens (or creates) the event service of the engine `name`: every event about its inputs, in
/// order. A subscriber that falls behind loses the oldest events, not the newest.
pub(crate) fn open_engine_events(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, EventPayload, EventRecord>> {
    node.service_builder(&naming::engine_events_service(name)?)
        .publish_subscribe::<EventPayload>()
        .user_header::<EventRecord>()
        .max_publishers(EVENT_PUBLISHERS)
        .max_subscribers(EVENT_SUBSCRIBERS)
        .subscriber_max_buffer_size(EVENT_BUFFER_SIZE)
        .enable_safe_overflow(true)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open events of engine {name:?}: {e}")))
}

#[cfg(test)]
mod tests {
    use iceoryx2_bb_container::{relocatable_option::RelocatableOption, vector::Vector};

    use super::*;
    use crate::{
        ipc::types::{EngineName, EventKind, FlowName, Forward, PayloadType},
        routing::ids::{EdgeId, InputId, OfferId, ProducerName},
        test_util::unique,
    };

    const PAYLOAD: [u8; 4] = [1, 2, 3, 4];
    const PRODUCER: &str = "camera";
    const SESSION: u64 = 1;
    const SEQ: u64 = 7;

    #[test]
    fn engine_input_carries_its_header_and_payload() {
        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let service = open_engine_input(&node, &unique("engine")).unwrap();
        let publisher = service
            .publisher_builder()
            .initial_max_slice_len(PAYLOAD.len())
            .create()
            .unwrap();
        let subscriber = service.subscriber_builder().create().unwrap();
        let offer = OfferId {
            session: SESSION,
            edge: EdgeId(1),
            seq: SEQ,
        };
        let id = InputId {
            producer: ProducerName::try_from(PRODUCER).unwrap(),
            session: SESSION,
            seq: SEQ,
        };

        let mut sample = publisher.loan_slice_uninit(PAYLOAD.len()).unwrap();
        *sample.user_header_mut() = InputHeader {
            offer,
            id,
            payload_type: PayloadType::new(),
        };
        let sample = sample.write_from_slice(&PAYLOAD);
        sample.send().unwrap();

        let received = subscriber.receive().unwrap().unwrap();
        assert_eq!(received.user_header().offer, offer);
        assert_eq!(received.user_header().id, id);
        assert_eq!(received.payload(), &PAYLOAD);
    }

    #[test]
    fn engine_event_carries_its_record_and_result() {
        const RESULT: [u8; 2] = [9, 8];
        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let service = open_engine_events(&node, &unique("engine")).unwrap();
        let publisher = service
            .publisher_builder()
            .initial_max_slice_len(RESULT.len())
            .create()
            .unwrap();
        let subscriber = service.subscriber_builder().create().unwrap();
        let offer = OfferId {
            session: SESSION,
            edge: EdgeId(1),
            seq: SEQ,
        };
        let id = InputId {
            producer: ProducerName::try_from(PRODUCER).unwrap(),
            session: SESSION,
            seq: SEQ,
        };
        let engine = EngineName::try_from("detector").unwrap();
        let flow = FlowName::try_from("tracking").unwrap();
        let record = EventRecord::result(
            offer,
            id,
            engine,
            flow,
            Forward::Pass,
            &[],
            PayloadType::new(),
        )
        .unwrap();

        let mut sample = publisher.loan_slice_uninit(RESULT.len()).unwrap();
        *sample.user_header_mut() = record;
        let sample = sample.write_from_slice(&RESULT);
        sample.send().unwrap();

        let received = subscriber.receive().unwrap().unwrap();
        assert_eq!(received.user_header().kind, EventKind::Result);
        assert_eq!(received.user_header().offer, RelocatableOption::Some(offer));
        assert_eq!(received.payload(), &RESULT);
    }

    #[test]
    fn engine_outcome_carries_its_record_output_and_result() {
        const RESULT: [u8; 2] = [9, 8];
        const OUTPUT: [u8; 3] = [1, 2, 3];
        let payload: Vec<u8> = OUTPUT.iter().chain(&RESULT).copied().collect();
        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let service = open_engine_outcome(&node, &unique("engine")).unwrap();
        let publisher = service
            .publisher_builder()
            .initial_max_slice_len(payload.len())
            .create()
            .unwrap();
        let subscriber = service.subscriber_builder().create().unwrap();
        let offer = OfferId {
            session: SESSION,
            edge: EdgeId(1),
            seq: SEQ,
        };
        let id = InputId {
            producer: ProducerName::try_from(PRODUCER).unwrap(),
            session: SESSION,
            seq: SEQ,
        };
        let record = OutcomeRecord::emit(
            offer,
            id,
            PayloadType::new(),
            PayloadType::new(),
            OUTPUT.len() as u32,
            RESULT.len() as u32,
        )
        .unwrap();

        let mut sample = publisher.loan_slice_uninit(payload.len()).unwrap();
        *sample.user_header_mut() = record;
        sample.write_from_slice(&payload).send().unwrap();

        let received = subscriber.receive().unwrap().unwrap();
        let header = received.user_header();
        assert_eq!(header.forward, Forward::Emit);
        assert_eq!(header.result_len as usize, RESULT.len());
        assert_eq!(header.output_len as usize, OUTPUT.len());
        assert_eq!(received.payload(), payload.as_slice());
    }
}
