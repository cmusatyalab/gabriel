//! Fixed-layout types that cross iceoryx2 services between the client, the servers and the
//! engines. Payload bytes travel next to them as the sample's slice; these carry the metadata.

use std::sync::Arc;

use iceoryx2::prelude::ZeroCopySend;
use iceoryx2_bb_container::{
    relocatable_option::RelocatableOption, string::StaticString, vector::StaticVec,
};
use serde::{Deserialize, Serialize};

use crate::{
    Error, Result,
    clock::Timestamp,
    envelope::MAX_FLOW_NAME,
    routing::ids::{InputId, OfferId},
};

/// Maximum length of a payload type name, e.g. a protobuf full name.
pub const MAX_PAYLOAD_TYPE_NAME: usize = 64;
/// Maximum length of an engine name.
pub const MAX_ENGINE_NAME: usize = 64;
/// Maximum number of offers one event creates: one per target engine of a crossing, or one per
/// outgoing edge after a pass.
pub const MAX_CHILDREN: usize = 16;

/// How to read a payload's bytes, so a consumer can pick the right decoder.
pub(crate) type PayloadType = StaticString<MAX_PAYLOAD_TYPE_NAME>;

/// Converts a payload type name from the caller. An empty name means no type.
pub(crate) fn payload_type(name: &str) -> Result<PayloadType> {
    PayloadType::try_from(name).map_err(|_| {
        Error::InvalidArgument(format!(
            "payload type {name:?} is longer than {MAX_PAYLOAD_TYPE_NAME} bytes"
        ))
    })
}

/// The name of an engine.
pub(crate) type EngineName = StaticString<MAX_ENGINE_NAME>;

/// The name of a flow. One input can enter several flows under the same `InputId`, so every event
/// says which flow it belongs to.
pub(crate) type FlowName = StaticString<MAX_FLOW_NAME>;

/// Metadata of an input offered to an engine: the user header of the engine's input service.
/// The payload is the slice that follows: the input's bytes, which only `payload_type` explains.
#[derive(Debug, Clone, Default, ZeroCopySend)]
#[repr(C)]
pub(crate) struct InputHeader {
    /// The offer being made. The engine's outcome ends this offer.
    pub(crate) offer: OfferId,
    /// The input the offer is for.
    pub(crate) id: InputId,
    /// How to read the payload.
    pub(crate) payload_type: PayloadType,
}

/// What a client tells a server about one offer on a crossing.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ZeroCopySend, Serialize, Deserialize)]
#[repr(C)]
pub(crate) enum CrossingKind {
    /// The offer was sent: the payload is the input.
    #[default]
    Input,
    /// The offer was replaced in the crossing's pending slot on the client, and will never be
    /// sent. The server tells the subscribers of the crossing's target engines. No payload.
    Skipped,
}

/// Metadata of an offer a client sends on a crossing: the user header of a server's input service.
/// The payload is the input's bytes.
#[derive(Debug, Clone, Default, PartialEq, ZeroCopySend, Serialize, Deserialize)]
#[repr(C)]
pub(crate) struct CrossingHeader {
    pub(crate) kind: CrossingKind,
    /// The session the server gave the flow when the client installed it.
    pub(crate) flow: u64,
    /// The client's offer. Its edge is the crossing; the server knows the crossing's targets.
    pub(crate) offer: OfferId,
    pub(crate) id: InputId,
    /// How to read the payload.
    pub(crate) payload_type: PayloadType,
}

/// What happened to an offer or an input.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ZeroCopySend, Serialize, Deserialize)]
#[repr(C)]
pub enum EventKind {
    /// A server received a crossing and created `children`, one offer per target engine.
    #[default]
    Accepted,
    /// The offer was sent to the engine; a result may come.
    Sent,
    /// The engine finished the input. `forward` says what it passed on.
    Result,
    /// The offer was replaced in a pending slot and never sent.
    Skipped,
    /// The engine did not answer in time, or was lost.
    TimedOut,
    /// The client lost its connection to a server of the flow. Every offer on that server ended
    /// with it, including offers whose events were lost. It names no input.
    Disconnected,
    /// A result an engine derived from subscribed events. It answers no offer.
    Derived,
}

/// What an engine forwards along its outgoing input edges.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, ZeroCopySend, Serialize, Deserialize)]
#[repr(C)]
pub enum Forward {
    #[default]
    Stop,
    Pass,
    Emit,
}

/// Finds the events a subscriber lost, from the sequence numbers of the events it reads.
#[derive(Debug, Default)]
pub(crate) struct EventGaps {
    /// The sequence number the next event should have. `None` until the first event, because a
    /// subscriber can start in the middle of the stream.
    next: Option<u64>,
}

impl EventGaps {
    /// Notes the event numbered `seq`, and returns how many events came before it that this
    /// subscriber never saw.
    pub(crate) fn observe(&mut self, seq: u64) -> u64 {
        let lost = self.next.map_or(0, |next| seq.saturating_sub(next));
        self.next = Some(seq + 1);
        lost
    }
}

/// Metadata of an event: the user header of an engine's event service. The result bytes, if any,
/// are the slice that follows.
///
/// `late` and `Lost` of the app-facing event are worked out by the reader: a result is late when
/// the reader already saw its offer end, and events are lost when their numbers skip.
#[derive(Debug, Clone, Default, PartialEq, ZeroCopySend, Serialize, Deserialize)]
#[repr(C)]
pub(crate) struct EventRecord {
    /// Counts the events published on this engine's event service, from 1. A subscriber that
    /// sees a gap knows it lost events. Set by the publisher, with [`EventRecord::numbered`].
    pub(crate) seq: u64,
    pub(crate) kind: EventKind,
    /// The offer that ended. `None` for `Derived`, which answers no offer.
    pub(crate) offer: RelocatableOption<OfferId>,
    pub(crate) id: InputId,
    /// The engine that published the event.
    pub(crate) engine: EngineName,
    /// The flow the offer belongs to, or for `Derived`, the flow the engine derived it in.
    pub(crate) flow: FlowName,
    /// Only for `Result`.
    pub(crate) forward: Forward,
    /// The offers created by an `Accepted`, or by a `Result` that forwarded. Empty otherwise.
    pub(crate) children: StaticVec<OfferId, MAX_CHILDREN>,
    /// How to read the result bytes. Empty when there are none.
    pub(crate) payload_type: PayloadType,
    /// When the event happened, on the clock of the machine whose client or server made it.
    pub(crate) at: Timestamp,
    /// When the offer that ended was made, if its maker knows: set by servers, for the events of
    /// their engines' offers.
    pub(crate) offered_at: RelocatableOption<Timestamp>,
    /// When the client received the event from the remote server, on the client's clock. `at` and
    /// `offered_at` are then on the remote server's clock. `None` for other events.
    pub(crate) received_at: RelocatableOption<Timestamp>,
}

impl EventRecord {
    /// This record as event number `seq` of its event service.
    pub(crate) fn numbered(mut self, seq: u64) -> Self {
        self.seq = seq;
        self
    }

    /// This record, for an offer made at `at`.
    pub(crate) fn offered_at(mut self, at: Timestamp) -> Self {
        self.offered_at = RelocatableOption::Some(at);
        self
    }

    /// A record of an event that happens now.
    fn new(
        kind: EventKind,
        offer: RelocatableOption<OfferId>,
        id: InputId,
        engine: EngineName,
        flow: FlowName,
    ) -> Self {
        Self {
            kind,
            offer,
            id,
            engine,
            flow,
            at: Timestamp::now(),
            ..Self::default()
        }
    }

    /// A server received a crossing and created the offers `children`.
    pub(crate) fn accepted(
        offer: OfferId,
        id: InputId,
        engine: EngineName,
        flow: FlowName,
        children: &[OfferId],
    ) -> Result<Self> {
        let mut record = Self::new(
            EventKind::Accepted,
            RelocatableOption::Some(offer),
            id,
            engine,
            flow,
        );
        record.children = children_from(children)?;
        Ok(record)
    }

    /// The offer was sent to the engine.
    pub(crate) fn sent(offer: OfferId, id: InputId, engine: EngineName, flow: FlowName) -> Self {
        Self::new(
            EventKind::Sent,
            RelocatableOption::Some(offer),
            id,
            engine,
            flow,
        )
    }

    /// The engine finished the input. `children` are the offers created by passing it on; they
    /// must be empty for `Forward::Stop`. `payload_type` says how to read the result bytes.
    pub(crate) fn result(
        offer: OfferId,
        id: InputId,
        engine: EngineName,
        flow: FlowName,
        forward: Forward,
        children: &[OfferId],
        payload_type: PayloadType,
    ) -> Result<Self> {
        if forward == Forward::Stop && !children.is_empty() {
            return Err(Error::InvalidArgument(String::from(
                "a stopped input cannot have children",
            )));
        }
        let mut record = Self::new(
            EventKind::Result,
            RelocatableOption::Some(offer),
            id,
            engine,
            flow,
        );
        record.forward = forward;
        record.children = children_from(children)?;
        record.payload_type = payload_type;
        Ok(record)
    }

    /// The offer was replaced in a pending slot and never sent.
    pub(crate) fn skipped(offer: OfferId, id: InputId, engine: EngineName, flow: FlowName) -> Self {
        Self::new(
            EventKind::Skipped,
            RelocatableOption::Some(offer),
            id,
            engine,
            flow,
        )
    }

    /// The engine did not answer in time, or was lost.
    pub(crate) fn timed_out(
        offer: OfferId,
        id: InputId,
        engine: EngineName,
        flow: FlowName,
    ) -> Self {
        Self::new(
            EventKind::TimedOut,
            RelocatableOption::Some(offer),
            id,
            engine,
            flow,
        )
    }

    /// The client lost its connection to the server of the flow `flow`, and ended every offer
    /// there.
    pub(crate) fn disconnected(flow: FlowName) -> Self {
        Self::new(
            EventKind::Disconnected,
            RelocatableOption::None,
            InputId::default(),
            EngineName::new(),
            flow,
        )
    }

    /// A result the engine derived from subscribed events, for the input `id`. It answers no
    /// offer.
    pub(crate) fn derived(
        id: InputId,
        engine: EngineName,
        flow: FlowName,
        payload_type: PayloadType,
    ) -> Self {
        let mut record = Self::new(
            EventKind::Derived,
            RelocatableOption::None,
            id,
            engine,
            flow,
        );
        record.payload_type = payload_type;
        record
    }
}

fn children_from(children: &[OfferId]) -> Result<StaticVec<OfferId, MAX_CHILDREN>> {
    StaticVec::try_from(children).map_err(|_| {
        Error::InvalidArgument(format!(
            "{} children exceed the maximum of {MAX_CHILDREN}",
            children.len()
        ))
    })
}

/// The bytes of an input or a result, with how to read them. Cloning shares the bytes.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Data {
    pub(crate) bytes: Arc<[u8]>,
    pub(crate) payload_type: PayloadType,
}

/// An event for the client that installed a flow, as its server sends it.
///
/// The payload is the input the engine forwarded, when the client needs it and does not hold it
/// (`InstallFlow::client_sources`), then the result bytes.
#[derive(Debug, Clone, Default, PartialEq, ZeroCopySend, Serialize, Deserialize)]
#[repr(C)]
pub(crate) struct ClientEventHeader {
    pub(crate) record: EventRecord,
    /// How to read the forwarded input. Empty when there is none.
    pub(crate) forwarded_type: PayloadType,
    /// The length in bytes of the forwarded input, at the start of the payload. 0 when there is
    /// none: an emitted input is never empty.
    pub(crate) forwarded_len: u64,
}

impl ClientEventHeader {
    /// The header of `record`, sent with `forwarded`, if any.
    pub(crate) fn new(record: EventRecord, forwarded: Option<&Data>) -> Self {
        Self {
            record,
            forwarded_type: forwarded.map_or_else(PayloadType::new, |data| data.payload_type),
            forwarded_len: forwarded.map_or(0, |data| data.bytes.len() as u64),
        }
    }

    /// Splits `payload` into the forwarded input, if any, and the result bytes. Fails if the
    /// payload is shorter than the header says.
    pub(crate) fn split(self, payload: &[u8]) -> Result<(EventRecord, Option<Data>, &[u8])> {
        let forwarded_len = usize::try_from(self.forwarded_len)
            .ok()
            .filter(|len| *len <= payload.len())
            .ok_or_else(|| {
                Error::InvalidArgument(format!(
                    "an event says it carries {} forwarded bytes, but has {}",
                    self.forwarded_len,
                    payload.len()
                ))
            })?;
        let (forwarded, result) = payload.split_at(forwarded_len);
        let forwarded = (!forwarded.is_empty()).then(|| Data {
            bytes: Arc::from(forwarded),
            payload_type: self.forwarded_type,
        });
        Ok((self.record, forwarded, result))
    }
}

/// What an engine reports to its server about one offered input. The server turns it into an
/// `EventRecord`, after it has freed the edge and created the offers that follow.
///
/// The payload is the engine's new output, then the result bytes. Only `Emit` has an output. The
/// result comes last because an engine knows it only when it reports the outcome, after it has
/// written the output in place.
#[derive(Debug, Clone, Default, ZeroCopySend)]
#[repr(C)]
pub(crate) struct OutcomeRecord {
    /// The offer the engine is answering.
    pub(crate) offer: OfferId,
    pub(crate) id: InputId,
    pub(crate) forward: Forward,
    /// How to read the result bytes. Empty when there are none.
    pub(crate) payload_type: PayloadType,
    /// How to read the output. Empty unless `forward` is `Emit`.
    pub(crate) output_type: PayloadType,
    /// The length in bytes of the output, at the start of the payload.
    pub(crate) output_len: u32,
    /// The length in bytes of the result, after the output.
    pub(crate) result_len: u32,
}

impl OutcomeRecord {
    /// Forward nothing. A discard is a stop with no result. `result_type` says how to read the
    /// result.
    pub(crate) fn stop(
        offer: OfferId,
        id: InputId,
        result_type: PayloadType,
        result_len: u32,
    ) -> Self {
        Self {
            offer,
            id,
            forward: Forward::Stop,
            payload_type: result_type,
            result_len,
            ..Self::default()
        }
    }

    /// Forward the input unchanged along every outgoing edge.
    pub(crate) fn pass(
        offer: OfferId,
        id: InputId,
        result_type: PayloadType,
        result_len: u32,
    ) -> Self {
        Self {
            forward: Forward::Pass,
            ..Self::stop(offer, id, result_type, result_len)
        }
    }

    /// Forward a new input of `output_len` bytes along every outgoing edge. `output_type` says how
    /// to read it, `result_type` how to read the result.
    pub(crate) fn emit(
        offer: OfferId,
        id: InputId,
        result_type: PayloadType,
        output_type: PayloadType,
        output_len: u32,
        result_len: u32,
    ) -> Result<Self> {
        if output_len == 0 {
            return Err(Error::InvalidArgument(String::from(
                "an emit needs a non-empty output",
            )));
        }
        Ok(Self {
            forward: Forward::Emit,
            output_type,
            output_len,
            ..Self::stop(offer, id, result_type, result_len)
        })
    }
}

#[cfg(test)]
mod tests {
    use iceoryx2_bb_container::vector::Vector;

    use super::*;
    use crate::routing::ids::{EdgeId, ProducerName};

    const SESSION: u64 = 1;
    const SEQ: u64 = 1;
    const EDGE: EdgeId = EdgeId(1);

    fn offer(seq: u64) -> OfferId {
        OfferId {
            session: SESSION,
            edge: EDGE,
            seq,
        }
    }

    fn input() -> InputId {
        InputId {
            producer: ProducerName::try_from("camera").unwrap(),
            session: SESSION,
            seq: SEQ,
        }
    }

    fn engine() -> EngineName {
        EngineName::try_from("detector").unwrap()
    }

    fn flow() -> FlowName {
        FlowName::try_from("tracking").unwrap()
    }

    #[test]
    fn result_that_passes_keeps_its_children() {
        let children = [offer(2), offer(3)];

        let record = EventRecord::result(
            offer(1),
            input(),
            engine(),
            flow(),
            Forward::Pass,
            &children,
            PayloadType::new(),
        )
        .unwrap();

        assert_eq!(record.kind, EventKind::Result);
        assert_eq!(record.offer, RelocatableOption::Some(offer(1)));
        assert_eq!(record.children.as_slice(), &children);
    }

    #[test]
    fn stopped_result_cannot_have_children() {
        let result = EventRecord::result(
            offer(1),
            input(),
            engine(),
            flow(),
            Forward::Stop,
            &[offer(2)],
            PayloadType::new(),
        );

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn too_many_children_are_rejected() {
        let children = vec![offer(2); MAX_CHILDREN + 1];

        let result = EventRecord::accepted(offer(1), input(), engine(), flow(), &children);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn client_event_splits_into_the_forwarded_input_and_the_result() {
        const TILES: &[u8] = b"tiles";
        const RESULT: &[u8] = b"result";
        let forwarded = Data {
            bytes: Arc::from(TILES),
            payload_type: payload_type("tiles").unwrap(),
        };
        let header = ClientEventHeader::new(EventRecord::default(), Some(&forwarded));
        let payload = [TILES, RESULT].concat();

        let (_, split_forwarded, result) = header.split(&payload).unwrap();

        assert_eq!(split_forwarded, Some(forwarded));
        assert_eq!(result, RESULT);
    }

    #[test]
    fn client_event_without_a_forwarded_input_is_all_result() {
        let header = ClientEventHeader::new(EventRecord::default(), None);

        let (_, forwarded, result) = header.split(b"result").unwrap();

        assert_eq!(forwarded, None);
        assert_eq!(result, b"result");
    }

    #[test]
    fn client_event_shorter_than_its_header_says_is_rejected() {
        let header = ClientEventHeader {
            forwarded_len: 10,
            ..ClientEventHeader::default()
        };

        assert!(header.split(b"short").is_err());
    }

    #[test]
    fn emit_needs_a_non_empty_output() {
        let result = OutcomeRecord::emit(
            offer(1),
            input(),
            PayloadType::new(),
            PayloadType::new(),
            0,
            0,
        );

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn first_event_has_no_gap() {
        let mut gaps = EventGaps::default();

        assert_eq!(gaps.observe(40), 0);
    }

    #[test]
    fn consecutive_events_have_no_gap() {
        let mut gaps = EventGaps::default();
        gaps.observe(1);

        assert_eq!(gaps.observe(2), 0);
    }

    #[test]
    fn skipped_events_are_counted_as_lost() {
        let mut gaps = EventGaps::default();
        gaps.observe(1);

        assert_eq!(gaps.observe(5), 3);
        assert_eq!(gaps.observe(6), 0);
    }

    #[test]
    fn payload_type_longer_than_the_maximum_is_rejected() {
        let name = "x".repeat(MAX_PAYLOAD_TYPE_NAME + 1);

        assert!(matches!(
            payload_type(&name),
            Err(Error::InvalidArgument(_))
        ));
        assert!(payload_type("patchcore.v1.Score").is_ok());
    }

    #[test]
    fn numbered_sets_the_sequence_number() {
        let record = EventRecord::sent(offer(1), input(), engine(), flow()).numbered(9);

        assert_eq!(record.seq, 9);
    }

    #[test]
    fn derived_answers_no_offer() {
        let record = EventRecord::derived(input(), engine(), flow(), PayloadType::new());

        assert_eq!(record.kind, EventKind::Derived);
        assert_eq!(record.offer, RelocatableOption::None);
    }
}
