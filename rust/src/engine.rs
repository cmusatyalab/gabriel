//! Engine: takes inputs from its server and reports one outcome for each.

use std::mem::MaybeUninit;

use iceoryx2::port::{publisher::Publisher, subscriber::Subscriber};
use iceoryx2::prelude::*;
use iceoryx2::sample::Sample;
use iceoryx2::sample_mut_uninit::SampleMutUninit;
use iceoryx2_bb_container::vector::Vector;

use crate::ipc::types::{InputHeader, MAX_PARTS, OutcomeRecord, PayloadType};
use crate::routing::ids::InputId;
use crate::{Error, Result, services};

/// One part of an input: a view into shared memory, valid until the engine reports its outcome.
#[derive(Debug)]
#[repr(C)]
pub struct Part {
    pub(crate) buf: *const u8,
    pub(crate) len: usize,
}

impl Part {
    /// The bytes of the part.
    pub fn as_bytes(&self) -> &[u8] {
        // SAFETY: `buf` and `len` describe a range of the current input's sample, which the
        // engine keeps alive until the outcome. Parts are only handed out inside an `Input`,
        // which borrows the engine, and `Part` cannot be copied out of it.
        unsafe { std::slice::from_raw_parts(self.buf, self.len) }
    }
}

/// An input held by the engine until it reports its outcome.
#[derive(Debug)]
pub struct Input<'a> {
    pub(crate) id: &'a InputId,
    /// One part, unless an upstream engine emitted several.
    pub parts: &'a [Part],
}

/// The input being processed, and the views of its parts that C holds.
struct Current {
    sample: Sample<ipc::Service, [u8], InputHeader>,
    parts: Vec<Part>,
}

/// An outcome being built by [`Engine::new_output`] and [`Engine::add_output`].
struct Emitting {
    sample: SampleMutUninit<ipc::Service, [MaybeUninit<u8>], OutcomeRecord>,
    part_lengths: Vec<u32>,
    /// Bytes of the payload taken by the parts so far.
    used: usize,
}

/// Processes one input at a time and reports exactly one outcome for it: [`Engine::stop`],
/// [`Engine::pass`], or [`Engine::emit`].
pub struct Engine {
    max_output_size: usize,
    #[allow(dead_code)] // keeps the services alive
    node: Node<ipc::Service>,
    inputs: Subscriber<ipc::Service, [u8], InputHeader>,
    outcomes: Publisher<ipc::Service, [u8], OutcomeRecord>,
    current: Option<Current>,
    emitting: Option<Emitting>,
}

impl Engine {
    /// Creates the engine `name`. The parts it emits, and its result, are together at most
    /// `max_output_size` bytes.
    pub fn new(name: &str, max_output_size: usize) -> Result<Self> {
        let node = NodeBuilder::new()
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        let inputs = services::open_engine_input(&node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create subscriber: {e}")))?;
        let outcomes = services::open_engine_outcome(&node, name)?
            .publisher_builder()
            .max_loaned_samples(1)
            .initial_max_slice_len(max_output_size)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create publisher: {e}")))?;
        Ok(Self {
            max_output_size,
            node,
            inputs,
            outcomes,
            current: None,
            emitting: None,
        })
    }

    /// Returns the next input, or [`Error::Again`] if none is available. The input stays valid
    /// until the engine reports its outcome.
    pub fn poll_input(&mut self) -> Result<Input<'_>> {
        if self.current.is_some() {
            return Err(Error::InvalidArgument(String::from(
                "the previous input has no outcome yet",
            )));
        }
        let sample = self
            .inputs
            .receive()
            .map_err(|e| Error::Internal(format!("failed to receive: {e}")))?
            .ok_or(Error::Again)?;

        let payload = sample.payload();
        let mut parts = Vec::with_capacity(sample.user_header().part_lengths.len());
        let mut offset = 0;
        for &len in sample.user_header().part_lengths.iter() {
            let len = len as usize;
            let bytes = payload
                .get(offset..offset + len)
                .ok_or_else(|| Error::Internal(String::from("input parts exceed the payload")))?;
            parts.push(Part {
                buf: bytes.as_ptr(),
                len,
            });
            offset += len;
        }

        let current = self.current.insert(Current { sample, parts });
        Ok(Input {
            id: &current.sample.user_header().id,
            parts: &current.parts,
        })
    }

    /// Reports that the engine forwards nothing. A discard is a stop with no result.
    pub fn stop(&mut self, result: &[u8]) -> Result<()> {
        self.report(result, OutcomeRecord::stop)
    }

    /// Reports that the engine forwards the input unchanged along every outgoing edge.
    pub fn pass(&mut self, result: &[u8]) -> Result<()> {
        self.report(result, OutcomeRecord::pass)
    }

    /// Loans the buffer for the next emitted part, which is whatever is left of
    /// `max_output_size`. Fill it, then call [`Engine::add_output`].
    pub fn new_output(&mut self) -> Result<&mut [MaybeUninit<u8>]> {
        self.current_input()?;
        if self.emitting.is_none() {
            let sample = self
                .outcomes
                .loan_slice_uninit(self.max_output_size)
                .map_err(|e| Error::Internal(format!("failed to loan: {e}")))?;
            self.emitting = Some(Emitting {
                sample,
                part_lengths: Vec::new(),
                used: 0,
            });
        }
        let emitting = self.emitting.as_mut().expect("just created");
        let used = emitting.used;
        Ok(&mut emitting.sample.payload_mut()[used..])
    }

    /// Adds the first `len` bytes of the buffer from [`Engine::new_output`] as the next part.
    pub fn add_output(&mut self, len: usize) -> Result<()> {
        let max_output_size = self.max_output_size;
        let emitting = self.emitting.as_mut().ok_or_else(|| {
            Error::InvalidArgument(String::from("no output; call new_output first"))
        })?;
        if emitting.part_lengths.len() == MAX_PARTS {
            return Err(Error::InvalidArgument(format!(
                "an input has at most {MAX_PARTS} parts"
            )));
        }
        if emitting.used + len > max_output_size {
            return Err(Error::SizeExceeded {
                size: emitting.used + len,
                max: max_output_size,
            });
        }
        emitting.part_lengths.push(len as u32);
        emitting.used += len;
        Ok(())
    }

    /// Reports that the engine forwards the parts added so far along every outgoing edge.
    pub fn emit(&mut self, result: &[u8]) -> Result<()> {
        let (offer, id) = self.current_input()?;
        let mut emitting = self.emitting.take().ok_or_else(|| {
            Error::InvalidArgument(String::from("nothing to emit; add an output first"))
        })?;
        let end = emitting.used + result.len();
        if end > self.max_output_size {
            return Err(Error::SizeExceeded {
                size: end,
                max: self.max_output_size,
            });
        }
        let record = OutcomeRecord::emit(
            offer,
            id,
            PayloadType::new(),
            result.len() as u32,
            &emitting.part_lengths,
        )?;
        emitting.sample.payload_mut()[emitting.used..end].copy_from_slice(as_uninit(result));
        *emitting.sample.user_header_mut() = record;
        self.send(emitting.sample)
    }

    /// Reports `stop` or `pass`, made by `record`, with `result` as the payload.
    fn report(
        &mut self,
        result: &[u8],
        record: fn(crate::routing::ids::OfferId, InputId, PayloadType, u32) -> OutcomeRecord,
    ) -> Result<()> {
        let (offer, id) = self.current_input()?;
        if result.len() > self.max_output_size {
            return Err(Error::SizeExceeded {
                size: result.len(),
                max: self.max_output_size,
            });
        }
        self.emitting = None;
        let mut sample = self
            .outcomes
            .loan_slice_uninit(result.len())
            .map_err(|e| Error::Internal(format!("failed to loan: {e}")))?;
        *sample.user_header_mut() = record(offer, id, PayloadType::new(), result.len() as u32);
        sample.payload_mut().copy_from_slice(as_uninit(result));
        self.send(sample)
    }

    /// The offer and id of the input being processed.
    fn current_input(&self) -> Result<(crate::routing::ids::OfferId, InputId)> {
        let current = self.current.as_ref().ok_or_else(|| {
            Error::InvalidArgument(String::from("no input to answer; call poll_input first"))
        })?;
        let header = current.sample.user_header();
        Ok((header.offer, header.id))
    }

    /// Sends the outcome and releases the input it answers.
    fn send(
        &mut self,
        sample: SampleMutUninit<ipc::Service, [MaybeUninit<u8>], OutcomeRecord>,
    ) -> Result<()> {
        // SAFETY: the caller wrote the header and the payload bytes the header describes. The
        // server only reads those, never the rest of the slice.
        unsafe { sample.assume_init() }
            .send()
            .map_err(|e| Error::Internal(format!("failed to send outcome: {e}")))?;
        self.current = None;
        Ok(())
    }
}

/// Views initialized bytes as possibly uninitialized ones, for copying into a loaned buffer.
fn as_uninit(bytes: &[u8]) -> &[MaybeUninit<u8>] {
    // SAFETY: `MaybeUninit<u8>` has the same layout as `u8`, and every `u8` is a valid one.
    unsafe { std::slice::from_raw_parts(bytes.as_ptr().cast(), bytes.len()) }
}

#[cfg(test)]
mod tests {
    use iceoryx2::port::{publisher::Publisher, subscriber::Subscriber};
    use iceoryx2_bb_container::{string::String as _, vector::Vector};

    use super::*;
    use crate::ipc::types::Forward;
    use crate::routing::ids::{EdgeId, OfferId, ProducerName};
    use crate::test_util::unique;

    const MAX_OUTPUT_SIZE: usize = 16;
    const SESSION: u64 = 1;
    const SEQ: u64 = 7;
    const PRODUCER: &str = "camera";
    const FRAME: [u8; 4] = [1, 2, 3, 4];
    const RESULT: [u8; 2] = [9, 8];

    /// An engine, and the server side of its services.
    struct Fixture {
        engine: Engine,
        inputs: Publisher<ipc::Service, [u8], InputHeader>,
        outcomes: Subscriber<ipc::Service, [u8], OutcomeRecord>,
    }

    impl Fixture {
        fn new() -> Self {
            let name = unique("engine");
            let engine = Engine::new(&name, MAX_OUTPUT_SIZE).unwrap();
            let inputs = services::open_engine_input(&engine.node, &name)
                .unwrap()
                .publisher_builder()
                .initial_max_slice_len(FRAME.len())
                .create()
                .unwrap();
            let outcomes = services::open_engine_outcome(&engine.node, &name)
                .unwrap()
                .subscriber_builder()
                .create()
                .unwrap();
            Self {
                engine,
                inputs,
                outcomes,
            }
        }

        /// Offers the engine an input of one part, `FRAME`.
        fn offer_frame(&self) {
            let header = InputHeader {
                offer: OfferId {
                    session: SESSION,
                    edge: EdgeId(1),
                    seq: SEQ,
                },
                id: InputId {
                    producer: ProducerName::try_from(PRODUCER).unwrap(),
                    session: SESSION,
                    seq: SEQ,
                },
                payload_type: PayloadType::new(),
                part_lengths: [FRAME.len() as u32].as_slice().try_into().unwrap(),
            };
            let mut sample = self.inputs.loan_slice_uninit(FRAME.len()).unwrap();
            *sample.user_header_mut() = header;
            sample.write_from_slice(&FRAME).send().unwrap();
        }
    }

    #[test]
    fn poll_without_an_input_is_again() {
        let mut fixture = Fixture::new();

        assert!(matches!(fixture.engine.poll_input(), Err(Error::Again)));
    }

    #[test]
    fn poll_returns_the_offered_input() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();

        let input = fixture.engine.poll_input().unwrap();

        assert_eq!(input.id.producer.as_str(), PRODUCER);
        assert_eq!(input.id.seq, SEQ);
        assert_eq!(input.parts.len(), 1);
        assert_eq!(input.parts[0].as_bytes(), FRAME);
    }

    #[test]
    fn next_input_waits_for_the_outcome() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();

        assert!(matches!(
            fixture.engine.poll_input(),
            Err(Error::InvalidArgument(_))
        ));
    }

    #[test]
    fn pass_reports_the_offer_and_result() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();

        fixture.engine.pass(&RESULT).unwrap();

        let outcome = fixture.outcomes.receive().unwrap().unwrap();
        let record = outcome.user_header();
        assert_eq!(record.forward, Forward::Pass);
        assert_eq!(record.offer.seq, SEQ);
        assert_eq!(record.id.seq, SEQ);
        assert_eq!(record.result_len as usize, RESULT.len());
        assert_eq!(&outcome.payload()[..RESULT.len()], RESULT);
        assert!(matches!(
            fixture.engine.pass(&RESULT),
            Err(Error::InvalidArgument(_))
        ));
    }

    #[test]
    fn discard_is_a_stop_with_no_result() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();

        fixture.engine.stop(&[]).unwrap();

        let outcome = fixture.outcomes.receive().unwrap().unwrap();
        assert_eq!(outcome.user_header().forward, Forward::Stop);
        assert_eq!(outcome.user_header().result_len, 0);
    }

    #[test]
    fn emit_reports_the_parts_then_the_result() {
        const TILE_A: [u8; 3] = [5, 5, 5];
        const TILE_B: [u8; 2] = [6, 6];
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();

        for tile in [&TILE_A[..], &TILE_B[..]] {
            let buf = fixture.engine.new_output().unwrap();
            for (slot, byte) in buf.iter_mut().zip(tile) {
                slot.write(*byte);
            }
            fixture.engine.add_output(tile.len()).unwrap();
        }
        fixture.engine.emit(&RESULT).unwrap();

        let outcome = fixture.outcomes.receive().unwrap().unwrap();
        let record = outcome.user_header();
        assert_eq!(record.forward, Forward::Emit);
        assert_eq!(
            record.part_lengths.as_slice(),
            &[TILE_A.len() as u32, TILE_B.len() as u32]
        );
        let payload = outcome.payload();
        assert_eq!(&payload[..TILE_A.len()], TILE_A);
        assert_eq!(&payload[TILE_A.len()..TILE_A.len() + TILE_B.len()], TILE_B);
        let result_at = TILE_A.len() + TILE_B.len();
        assert_eq!(&payload[result_at..result_at + RESULT.len()], RESULT);
    }

    #[test]
    fn output_larger_than_the_maximum_is_rejected() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();
        fixture.engine.new_output().unwrap();

        let result = fixture.engine.add_output(MAX_OUTPUT_SIZE + 1);

        assert!(matches!(result, Err(Error::SizeExceeded { .. })));
    }

    #[test]
    fn result_larger_than_the_maximum_is_rejected() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();

        let result = fixture.engine.stop(&[0; MAX_OUTPUT_SIZE + 1]);

        assert!(matches!(result, Err(Error::SizeExceeded { .. })));
    }
}
