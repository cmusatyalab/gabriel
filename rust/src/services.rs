//! iceoryx2 services that connect Gabriel's local components. Every side of a service opens it
//! through here, so they always agree on its types.

use std::{thread, time::Duration};

use iceoryx2::port::listener::{Listener, ListenerWaitError};
use iceoryx2::port::{BackpressureAction, BackpressureInfo};
use iceoryx2::prelude::*;
use iceoryx2::service::port_factory::{event, publish_subscribe, request_response};

use crate::envelope::Envelope;
use crate::ipc::types::{CrossingHeader, EventRecord, InputHeader, OutcomeRecord};
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

/// Inputs the server gives an engine before their outcomes arrive: only the one it works on. Every
/// other input waits at the server, where a newer one replaces an older one, so when the engine
/// finishes it starts on the newest input there is. An input given to the engine ahead of time could
/// not be replaced, and would be one processing time old when the engine started on it. The cost
/// is a short idle gap after each outcome, while the server delivers the next input.
pub(crate) const ENGINE_DELIVERY_LIMIT: usize = 1;
const _: () = assert!(ENGINE_DELIVERY_LIMIT <= ENGINE_INPUT_BUFFER_SIZE);

/// The capacity of each crossing from a client to its local server: the delivery limit plus 2. It
/// is the smallest that keeps the server's pending slots updating while every target of the
/// crossing is busy ("Freshness" in the design note).
pub(crate) const LOCAL_CROSSING_CAPACITY: usize = ENGINE_DELIVERY_LIMIT + 2;

/// Local clients one server serves at once: the publishers of its input service, and the clients
/// of its control service.
const MAX_LOCAL_CLIENTS: usize = 16;

/// Payload of a server's input service: the input's bytes, which its payload type explains.
pub(crate) type CrossingPayload = [u8];

/// Inputs from its clients that a server's input service holds until the server reads them. The
/// server reads them all each time a client wakes it, so they only pile up while it is busy with
/// something else. A full buffer fails the client's send, and the input's offer times out.
const SERVER_INPUT_BUFFER_SIZE: usize = 64;

/// Payload of a control call and of its reply: a postcard message ([`crate::wire`]).
pub(crate) type ControlPayload = [u8];

/// Payload of a client's event stream: the event's result bytes, if it has any.
pub(crate) type ClientEventPayload = [u8];

/// Events a client's stream holds until the client reads them. Past this the oldest are dropped,
/// and the client sees a gap in their sequence numbers. It is large because every event matters to
/// the client's token accounting.
const CLIENT_EVENT_BUFFER_SIZE: usize = 1024;

/// Payload of an engine's event service: the event's result bytes, if it has any.
pub(crate) type EventPayload = [u8];

/// Publishers of an event service. An engine's is published by the server for its own engines, or
/// by the client for engines it relays from a remote server, never both, so events have one order.
/// A producer's result service is published by the one client that takes its inputs.
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

/// Backpressure handler for the engine's input and outcome publishers. Their buffers must never
/// fill, because the server bounds what it delivers. If a bug breaks that bound, iceoryx2 would
/// by default wait until the receiver makes room, which hangs the sender. This handler drops the
/// sample and makes the send fail instead, so the bug becomes an error.
pub(crate) fn fail_when_full(_: &BackpressureInfo) -> BackpressureAction {
    BackpressureAction::DiscardDataAndFail
}

/// Opens (or creates) the event the server of the engine `name` signals after delivering an
/// input, so the engine can wait for inputs instead of polling.
pub(crate) fn open_engine_input_ready(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    open_ready(node, &naming::engine_input_ready_service(name)?, name)
}

/// Opens (or creates) the event the engine `name` signals after reporting an outcome, so its
/// server wakes up as soon as the engine is free.
pub(crate) fn open_engine_outcome_ready(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    open_ready(node, &naming::engine_outcome_ready_service(name)?, name)
}

/// Opens (or creates) an event between an engine and its server: one side signals, the other
/// listens.
fn open_ready(
    node: &Node<ipc::Service>,
    service: &ServiceName,
    engine: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    node.service_builder(service)
        .event()
        .max_notifiers(1)
        .max_listeners(1)
        .open_or_create()
        .map_err(|e| {
            Error::Internal(format!(
                "failed to open {service} of engine {engine:?}: {e}"
            ))
        })
}

/// Opens (or creates) the event service of the engine `name`: every event about its inputs, in
/// order. A subscriber that falls behind loses the oldest events, not the newest.
pub(crate) fn open_engine_events(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, EventPayload, EventRecord>> {
    open_events(node, &naming::engine_events_service(name)?)
}

/// Opens (or creates) the result service of the producer `name`: every event about its inputs, in
/// order, for the producer's app. Its one publisher is the client that takes the producer's
/// inputs. Like an engine's event service, it drops the oldest events for a subscriber that falls
/// behind.
pub(crate) fn open_producer_results(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, EventPayload, EventRecord>> {
    open_events(node, &naming::producer_result_service(name)?)
}

/// Opens (or creates) the event the client signals after publishing events about the producer
/// `name`'s inputs, so subscribers can wait for them.
pub(crate) fn open_producer_results_ready(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    let service = naming::producer_result_ready_service(name)?;
    node.service_builder(&service)
        .event()
        .max_notifiers(EVENT_PUBLISHERS)
        .max_listeners(EVENT_SUBSCRIBERS)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open {service}: {e}")))
}

/// Opens (or creates) an event service: one publisher, and subscribers that lose their oldest
/// events when they fall behind.
fn open_events(
    node: &Node<ipc::Service>,
    service: &ServiceName,
) -> Result<publish_subscribe::PortFactory<ipc::Service, EventPayload, EventRecord>> {
    node.service_builder(service)
        .publish_subscribe::<EventPayload>()
        .user_header::<EventRecord>()
        .max_publishers(EVENT_PUBLISHERS)
        .max_subscribers(EVENT_SUBSCRIBERS)
        .subscriber_max_buffer_size(EVENT_BUFFER_SIZE)
        .enable_safe_overflow(true)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open {service}: {e}")))
}

/// Opens (or creates) the control service of the server `name`. Each request is a postcard
/// [`Request`](crate::wire::Request), and each reply a postcard
/// [`Response`](crate::wire::Response).
pub(crate) fn open_server_control(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<request_response::PortFactory<ipc::Service, ControlPayload, (), ControlPayload, ()>> {
    node.service_builder(&naming::server_control_service(name)?)
        .request_response::<ControlPayload, ControlPayload>()
        .max_servers(1)
        .max_clients(MAX_LOCAL_CLIENTS)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open control of server {name:?}: {e}")))
}

/// Opens (or creates) the service local clients send the server `name` their inputs on. Each
/// sample is one input sent on a crossing. An input is never dropped, because its offer would then
/// never end before its deadline.
pub(crate) fn open_server_input(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, CrossingPayload, CrossingHeader>> {
    node.service_builder(&naming::server_input_service(name)?)
        .publish_subscribe::<CrossingPayload>()
        .user_header::<CrossingHeader>()
        .max_publishers(MAX_LOCAL_CLIENTS)
        .max_subscribers(1)
        .subscriber_max_buffer_size(SERVER_INPUT_BUFFER_SIZE)
        .enable_safe_overflow(false)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open input of server {name:?}: {e}")))
}

/// Opens (or creates) the event local clients signal after sending the server `name` an input.
pub(crate) fn open_server_input_ready(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    let service = naming::server_input_ready_service(name)?;
    node.service_builder(&service)
        .event()
        .max_notifiers(MAX_LOCAL_CLIENTS)
        .max_listeners(1)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open {service}: {e}")))
}

/// Opens (or creates) the stream of events the local server sends the client `name`: every event
/// about the client's flows, in order, numbered from 1. The client's token accounting reads it.
pub(crate) fn open_client_events(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<publish_subscribe::PortFactory<ipc::Service, ClientEventPayload, EventRecord>> {
    node.service_builder(&naming::client_events_service(name)?)
        .publish_subscribe::<ClientEventPayload>()
        .user_header::<EventRecord>()
        .max_publishers(1)
        .max_subscribers(1)
        .subscriber_max_buffer_size(CLIENT_EVENT_BUFFER_SIZE)
        .enable_safe_overflow(true)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open events of client {name:?}: {e}")))
}

/// Opens (or creates) the event the local server signals after sending the client `name` events.
pub(crate) fn open_client_events_ready(
    node: &Node<ipc::Service>,
    name: &str,
) -> Result<event::PortFactory<ipc::Service>> {
    let service = naming::client_events_ready_service(name)?;
    node.service_builder(&service)
        .event()
        .max_notifiers(1)
        .max_listeners(1)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open {service}: {e}")))
}

/// Opens (or creates) the event `service`, which a process's network thread signals to wake the
/// thread that reads what it received.
pub(crate) fn open_remote_ready(
    node: &Node<ipc::Service>,
    service: &ServiceName,
) -> Result<event::PortFactory<ipc::Service>> {
    node.service_builder(service)
        .event()
        .max_notifiers(1)
        .max_listeners(1)
        .open_or_create()
        .map_err(|e| Error::Internal(format!("failed to open {service}: {e}")))
}

/// Waits until `listener` has a notification, or `timeout` passes, and consumes the notifications.
/// Returns whether the wait ended early: for a notification, or for a signal. A signal is a
/// wake-up like any other, after which the caller checks again what it waits for; the Go runtime,
/// for one, signals its threads often.
pub(crate) fn wait_for_notification(
    listener: &Listener<ipc::Service>,
    timeout: Duration,
) -> std::result::Result<bool, ListenerWaitError> {
    // iceoryx2 waits without end when given no time.
    if timeout.is_zero() {
        return Ok(false);
    }
    match listener.timed_wait(|_| {}, timeout) {
        Ok(notifications) => Ok(notifications > 0),
        Err(ListenerWaitError::InterruptSignal) => Ok(true),
        Err(e) => Err(e),
    }
}

/// Waits until one of `listeners` has a notification, or `timeout` passes. Leaves the
/// notifications to be read by the listeners' owners, who also read what they announce.
pub(crate) fn wait_for_any<'a>(
    listeners: impl IntoIterator<Item = &'a Listener<ipc::Service>>,
    timeout: Duration,
) -> Result<()> {
    // Gabriel is a library: leave SIGINT/SIGTERM handling to the host application.
    let waitset = WaitSetBuilder::new()
        .signal_handling_mode(SignalHandlingMode::Disabled)
        .create::<ipc::Service>()
        .map_err(|e| Error::Internal(format!("failed to create waitset: {e}")))?;
    // Each guard keeps its listener attached for as long as it lives.
    let guards = listeners
        .into_iter()
        .map(|listener| waitset.attach_notification(listener))
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(|e| Error::Internal(format!("failed to attach a listener: {e}")))?;
    if guards.is_empty() {
        // Nothing can wake the caller early.
        thread::sleep(timeout);
        return Ok(());
    }
    waitset
        .wait_and_process_once_with_timeout(|_| CallbackProgression::Stop, timeout)
        .map_err(|e| Error::Internal(format!("failed to wait: {e}")))?;
    Ok(())
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
    fn full_engine_input_fails_the_send_instead_of_waiting() {
        let node = NodeBuilder::new().create::<ipc::Service>().unwrap();
        let service = open_engine_input(&node, &unique("engine")).unwrap();
        let publisher = service
            .publisher_builder()
            .initial_max_slice_len(PAYLOAD.len())
            .set_backpressure_handler(fail_when_full)
            .create()
            .unwrap();
        let _subscriber = service.subscriber_builder().create().unwrap();
        let send = || {
            let mut sample = publisher.loan_slice_uninit(PAYLOAD.len()).unwrap();
            *sample.user_header_mut() = InputHeader::default();
            sample.write_from_slice(&PAYLOAD).send()
        };

        for _ in 0..ENGINE_INPUT_BUFFER_SIZE {
            assert_eq!(send().unwrap(), 1);
        }

        assert!(send().is_err());
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
