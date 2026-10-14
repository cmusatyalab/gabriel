//! Engine: takes inputs from its server and reports one outcome for each.

use std::{mem::MaybeUninit, time::Duration};

use iceoryx2::port::{
    listener::Listener, notifier::Notifier, publisher::Publisher, subscriber::Subscriber,
};
use iceoryx2::prelude::*;
use iceoryx2::sample::Sample;
use iceoryx2::sample_mut_uninit::SampleMutUninit;

use crate::ipc::{
    as_uninit,
    types::{InputHeader, OutcomeRecord, PayloadType, payload_type},
};
use crate::routing::ids::{InputId, OfferId};
use crate::{Error, Result, naming, services};

/// An input held by the engine until it reports its outcome.
#[derive(Debug)]
pub struct Input<'a> {
    pub(crate) id: &'a InputId,
    pub(crate) payload_type: &'a PayloadType,
    /// The input's bytes, in shared memory. Only the payload type says how to read them.
    pub data: &'a [u8],
}

/// An outcome being built by [`Engine::new_output`] and [`Engine::emit`].
type Output = SampleMutUninit<ipc::Service, [MaybeUninit<u8>], OutcomeRecord>;

/// Processes one input at a time and reports exactly one outcome for it: [`Engine::stop`],
/// [`Engine::pass`], or [`Engine::emit`].
pub struct Engine {
    /// How to read this engine's results. Empty if it says nothing.
    result_type: PayloadType,
    max_output_size: usize,
    #[allow(dead_code)] // keeps the services alive
    node: Node<ipc::Service>,
    inputs: Subscriber<ipc::Service, [u8], InputHeader>,
    /// Signalled by the server after each input it delivers.
    input_ready: Listener<ipc::Service>,
    outcomes: Publisher<ipc::Service, [u8], OutcomeRecord>,
    /// Signalled after each outcome, so the server delivers the next input right away.
    outcome_ready: Notifier<ipc::Service>,
    /// The input being processed, until its outcome is reported.
    current: Option<Sample<ipc::Service, [u8], InputHeader>>,
    /// The output being written, until it is emitted.
    output: Option<Output>,
}

impl Engine {
    /// Creates the engine `name`. `result_type` names how to read its results, e.g. a protobuf
    /// message name; `None` if it has no convention. An output and its result are together at
    /// most `max_output_size` bytes.
    pub fn new(name: &str, result_type: Option<&str>, max_output_size: usize) -> Result<Self> {
        let result_type = payload_type(result_type.unwrap_or_default())?;
        // The server tells that the engine's process stopped by this node.
        let node = NodeBuilder::new()
            .name(&naming::engine_node(name)?)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        let inputs = services::open_engine_input(&node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create subscriber: {e}")))?;
        let input_ready = services::open_engine_input_ready(&node, name)?
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create listener: {e}")))?;
        let outcomes = services::open_engine_outcome(&node, name)?
            .publisher_builder()
            .max_loaned_samples(1)
            .initial_max_slice_len(max_output_size)
            .set_backpressure_handler(services::fail_when_full)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create publisher: {e}")))?;
        let outcome_ready = services::open_engine_outcome_ready(&node, name)?
            .notifier_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create notifier: {e}")))?;
        Ok(Self {
            result_type,
            max_output_size,
            node,
            inputs,
            input_ready,
            outcomes,
            outcome_ready,
            current: None,
            output: None,
        })
    }

    /// Waits until an input is ready, or `timeout` passes, and returns [`Error::Again`] then. The
    /// input itself comes from [`Engine::poll_input`]. A wake-up without an input is possible,
    /// so a caller polls in a loop.
    pub fn wait(&self, timeout: Duration) -> Result<()> {
        let failed = |e| Error::Internal(format!("failed to wait for an input: {e}"));
        // Notifications for inputs that are already here would wake the next wait for nothing.
        self.input_ready.try_wait(|_| {}).map_err(failed)?;
        let has_input = || {
            self.inputs
                .has_samples()
                .map_err(|e| Error::Internal(format!("failed to check for an input: {e}")))
        };
        if has_input()? {
            return Ok(());
        }
        // The server signals after it delivers, so an input that arrived since the check above
        // ends this wait at once.
        let woken = services::wait_for_notification(&self.input_ready, timeout).map_err(failed)?;
        if woken || has_input()? {
            Ok(())
        } else {
            Err(Error::Again)
        }
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
        let sample = self.current.insert(sample);
        Ok(Input {
            id: &sample.user_header().id,
            payload_type: &sample.user_header().payload_type,
            data: sample.payload(),
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

    /// Loans a buffer of `max_output_size` bytes for a new input. Fill it, then call
    /// [`Engine::emit`].
    pub fn new_output(&mut self) -> Result<&mut [MaybeUninit<u8>]> {
        self.current_input()?;
        if self.output.is_none() {
            let sample = self
                .outcomes
                .loan_slice_uninit(self.max_output_size)
                .map_err(|e| Error::Internal(format!("failed to loan: {e}")))?;
            self.output = Some(sample);
        }
        Ok(self.output.as_mut().expect("just created").payload_mut())
    }

    /// Reports that the engine forwards the first `output_len` bytes of the buffer from
    /// [`Engine::new_output`] as a new input along every outgoing edge. `output_type` names how
    /// to read it; empty if it has no convention.
    pub fn emit(&mut self, output_type: &str, output_len: usize, result: &[u8]) -> Result<()> {
        let (offer, id) = self.current_input()?;
        let output_type = payload_type(output_type)?;
        let mut output = self.output.take().ok_or_else(|| {
            Error::InvalidArgument(String::from("nothing to emit; call new_output first"))
        })?;
        let end = output_len + result.len();
        if end > self.max_output_size {
            return Err(Error::SizeExceeded {
                size: end,
                max: self.max_output_size,
            });
        }
        let record = OutcomeRecord::emit(
            offer,
            id,
            self.result_type_of(result),
            output_type,
            output_len as u32,
            result.len() as u32,
        )?;
        output.payload_mut()[output_len..end].copy_from_slice(as_uninit(result));
        *output.user_header_mut() = record;
        self.send(output)
    }

    /// Reports `stop` or `pass`, made by `record`, with `result` as the payload.
    fn report(
        &mut self,
        result: &[u8],
        record: fn(OfferId, InputId, PayloadType, u32) -> OutcomeRecord,
    ) -> Result<()> {
        let (offer, id) = self.current_input()?;
        if result.len() > self.max_output_size {
            return Err(Error::SizeExceeded {
                size: result.len(),
                max: self.max_output_size,
            });
        }
        self.output = None;
        let mut sample = self
            .outcomes
            .loan_slice_uninit(result.len())
            .map_err(|e| Error::Internal(format!("failed to loan: {e}")))?;
        *sample.user_header_mut() =
            record(offer, id, self.result_type_of(result), result.len() as u32);
        sample.payload_mut().copy_from_slice(as_uninit(result));
        self.send(sample)
    }

    /// The type of `result`: the engine's, or none when there is no result to read.
    fn result_type_of(&self, result: &[u8]) -> PayloadType {
        if result.is_empty() {
            PayloadType::new()
        } else {
            self.result_type
        }
    }

    /// The offer and id of the input being processed.
    fn current_input(&self) -> Result<(OfferId, InputId)> {
        let current = self.current.as_ref().ok_or_else(|| {
            Error::InvalidArgument(String::from("no input to answer; call poll_input first"))
        })?;
        let header = current.user_header();
        Ok((header.offer, header.id))
    }

    /// Sends the outcome, releases the input it answers, and wakes the server.
    fn send(&mut self, sample: Output) -> Result<()> {
        // The input is released even if the send fails: the outcome is gone either way, and the
        // engine must be able to take its next input.
        self.current = None;
        // SAFETY: the caller wrote the header and the payload bytes the header describes. The
        // server only reads those, never the rest of the slice.
        unsafe { sample.assume_init() }
            .send()
            .map_err(|e| Error::Internal(format!("failed to send outcome: {e}")))?;
        self.outcome_ready
            .notify()
            .map_err(|e| Error::Internal(format!("failed to notify the server: {e}")))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use iceoryx2_bb_container::string::String as _;

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
    const RESULT_TYPE: &str = "test.v1.Score";
    const OUTPUT_TYPE: &str = "test.v1.Tile";
    const FRAME_TYPE: &str = "test.v1.Frame";
    /// Long enough for a notification within one process, short enough to keep tests fast.
    const WAIT_TIMEOUT: Duration = Duration::from_millis(100);

    /// An engine, and the server side of its services.
    struct Fixture {
        engine: Engine,
        inputs: Publisher<ipc::Service, [u8], InputHeader>,
        input_ready: Notifier<ipc::Service>,
        outcomes: Subscriber<ipc::Service, [u8], OutcomeRecord>,
        outcome_ready: Listener<ipc::Service>,
    }

    impl Fixture {
        fn new() -> Self {
            let name = unique("engine");
            let engine = Engine::new(&name, Some(RESULT_TYPE), MAX_OUTPUT_SIZE).unwrap();
            let inputs = services::open_engine_input(&engine.node, &name)
                .unwrap()
                .publisher_builder()
                .initial_max_slice_len(FRAME.len())
                .create()
                .unwrap();
            let input_ready = services::open_engine_input_ready(&engine.node, &name)
                .unwrap()
                .notifier_builder()
                .create()
                .unwrap();
            let outcomes = services::open_engine_outcome(&engine.node, &name)
                .unwrap()
                .subscriber_builder()
                .create()
                .unwrap();
            let outcome_ready = services::open_engine_outcome_ready(&engine.node, &name)
                .unwrap()
                .listener_builder()
                .create()
                .unwrap();
            Self {
                engine,
                inputs,
                input_ready,
                outcomes,
                outcome_ready,
            }
        }

        /// Delivers the engine an input, `FRAME`, and signals it, as its server does.
        fn deliver_frame(&self) {
            self.offer_frame();
            self.input_ready.notify().unwrap();
        }

        /// Writes an input, `FRAME`, into the engine's input service without signalling it.
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
                payload_type: payload_type(FRAME_TYPE).unwrap(),
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
    fn wait_with_no_time_left_returns_at_once() {
        let fixture = Fixture::new();

        assert!(matches!(
            fixture.engine.wait(Duration::ZERO),
            Err(Error::Again)
        ));
    }

    #[test]
    fn wait_without_an_input_times_out() {
        let fixture = Fixture::new();

        assert!(matches!(
            fixture.engine.wait(WAIT_TIMEOUT),
            Err(Error::Again)
        ));
    }

    #[test]
    fn wait_returns_for_a_delivered_input() {
        let mut fixture = Fixture::new();
        fixture.deliver_frame();

        fixture.engine.wait(WAIT_TIMEOUT).unwrap();

        assert_eq!(fixture.engine.poll_input().unwrap().data, FRAME);
    }

    #[test]
    fn wait_returns_for_an_input_that_is_already_here() {
        let fixture = Fixture::new();
        fixture.offer_frame();

        fixture.engine.wait(WAIT_TIMEOUT).unwrap();
    }

    #[test]
    fn notifications_of_taken_inputs_do_not_wake_the_next_wait() {
        let mut fixture = Fixture::new();
        fixture.deliver_frame();
        fixture.engine.wait(WAIT_TIMEOUT).unwrap();
        fixture.engine.poll_input().unwrap();
        fixture.engine.stop(&[]).unwrap();
        fixture.input_ready.notify().unwrap();

        assert!(matches!(
            fixture.engine.wait(WAIT_TIMEOUT),
            Err(Error::Again)
        ));
    }

    #[test]
    fn outcome_wakes_the_server() {
        let mut fixture = Fixture::new();
        fixture.deliver_frame();
        fixture.engine.poll_input().unwrap();

        fixture.engine.stop(&[]).unwrap();

        let notifications = fixture
            .outcome_ready
            .timed_wait(|_| {}, WAIT_TIMEOUT)
            .unwrap();
        assert!(notifications > 0);
    }

    #[test]
    fn poll_returns_the_offered_input() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();

        let input = fixture.engine.poll_input().unwrap();

        assert_eq!(input.id.producer.as_str(), PRODUCER);
        assert_eq!(input.id.seq, SEQ);
        assert_eq!(input.payload_type.as_str(), FRAME_TYPE);
        assert_eq!(input.data, FRAME);
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
        assert_eq!(record.payload_type.as_str(), RESULT_TYPE);
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
        assert_eq!(outcome.user_header().payload_type.as_str(), "");
    }

    #[test]
    fn emit_reports_the_output_then_the_result() {
        const OUTPUT: [u8; 5] = [5, 5, 5, 6, 6];
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();

        let buf = fixture.engine.new_output().unwrap();
        for (slot, byte) in buf.iter_mut().zip(OUTPUT) {
            slot.write(byte);
        }
        fixture
            .engine
            .emit(OUTPUT_TYPE, OUTPUT.len(), &RESULT)
            .unwrap();

        let outcome = fixture.outcomes.receive().unwrap().unwrap();
        let record = outcome.user_header();
        assert_eq!(record.forward, Forward::Emit);
        assert_eq!(record.output_type.as_str(), OUTPUT_TYPE);
        assert_eq!(record.payload_type.as_str(), RESULT_TYPE);
        assert_eq!(record.output_len as usize, OUTPUT.len());
        assert_eq!(record.result_len as usize, RESULT.len());
        let payload = outcome.payload();
        assert_eq!(&payload[..OUTPUT.len()], OUTPUT);
        assert_eq!(&payload[OUTPUT.len()..OUTPUT.len() + RESULT.len()], RESULT);
    }

    #[test]
    fn emit_without_an_output_is_rejected() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();

        let result = fixture.engine.emit(OUTPUT_TYPE, 1, &[]);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn output_and_result_larger_than_the_maximum_are_rejected() {
        let mut fixture = Fixture::new();
        fixture.offer_frame();
        fixture.engine.poll_input().unwrap();
        fixture.engine.new_output().unwrap();

        let result = fixture.engine.emit(OUTPUT_TYPE, MAX_OUTPUT_SIZE, &RESULT);

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
