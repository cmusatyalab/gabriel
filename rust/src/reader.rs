//! Reader: reads the inputs a producer publishes, as the producer wrote them, alongside the client
//! that takes them. A reader sees every input, in order, unlike the client, which keeps only the
//! newest; one that falls behind loses the oldest, and learns how many from [`ReadInput::lost`].

use std::time::Duration;

use iceoryx2::port::{listener::Listener, subscriber::Subscriber};
use iceoryx2::prelude::*;
use iceoryx2::sample::Sample;
use iceoryx2_bb_container::string::String as _;

use crate::{
    Error, InputId, Result,
    envelope::Envelope,
    routing::ids::ProducerName,
    services::{self, InputPayload},
};

type InputSample = Sample<ipc::Service, InputPayload, Envelope>;

/// Reads the inputs of one producer. Only inputs published from now on are read.
pub struct Reader {
    #[allow(dead_code)] // keeps the ports alive
    node: Node<ipc::Service>,
    producer: ProducerName,
    inputs: Subscriber<ipc::Service, InputPayload, Envelope>,
    /// Signalled by the producer after each input.
    ready: Listener<ipc::Service>,
    /// The session and sequence number of the input read last, to tell how many were lost before
    /// the next one.
    last: Option<(u64, u64)>,
    /// The input returned last, kept so that it stays valid until the next poll.
    current: Option<InputSample>,
}

/// An input a reader read. Valid until the next poll.
#[derive(Debug)]
pub struct ReadInput<'a> {
    id: InputId,
    /// The producer's name, as the reader keeps it, for views that outlive `id`.
    pub(crate) producer: &'a ProducerName,
    pub(crate) envelope: &'a Envelope,
    data: &'a [u8],
    lost: u64,
}

impl ReadInput<'_> {
    /// The id the producer gave the input.
    pub fn id(&self) -> &InputId {
        &self.id
    }

    /// The flow the producer published the input on.
    pub fn flow(&self) -> &str {
        self.envelope.flow.as_str()
    }

    /// How to read [`ReadInput::data`]; empty if the input has no convention.
    pub fn payload_type(&self) -> &str {
        self.envelope.payload_type.as_str()
    }

    /// The input's bytes, in shared memory.
    pub fn data(&self) -> &[u8] {
        self.data
    }

    /// How many inputs of the producer's session were lost right before this one, because the
    /// reader fell behind. Inputs of a session that ended before this one are not counted.
    pub fn lost(&self) -> u64 {
        self.lost
    }
}

impl Reader {
    /// Reads the inputs of the producer `name`. The producer need not exist yet; inputs published
    /// from now on are read, also across its restarts.
    pub fn new(name: &str) -> Result<Self> {
        let producer = ProducerName::try_from(name)
            .map_err(|_| Error::InvalidArgument(format!("producer name {name:?} is too long")))?;
        let node = NodeBuilder::new()
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        let inputs = services::open_producer_input(&node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create subscriber: {e}")))?;
        let ready = services::open_producer_ready(&node, name)?
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create listener: {e}")))?;
        Ok(Self {
            node,
            producer,
            inputs,
            ready,
            last: None,
            current: None,
        })
    }

    /// Waits until an input is ready, or `timeout` passes, and returns [`Error::Again`] then. The
    /// input itself comes from [`Reader::poll`].
    pub fn wait(&self, timeout: Duration) -> Result<()> {
        // Consume the notifications for inputs that are already here, which would wake the next
        // wait for nothing.
        self.ready
            .try_wait(|_| {})
            .map_err(|e| Error::Internal(format!("failed to wait for an input: {e}")))?;
        if self.has_input()? {
            return Ok(());
        }
        // The producer signals after it publishes, so an input that arrived since the check above
        // ends this wait at once.
        let woken = services::wait_for_notification(&self.ready, timeout)
            .map_err(|e| Error::Internal(format!("failed to wait for an input: {e}")))?;
        if woken || self.has_input()? {
            Ok(())
        } else {
            Err(Error::Again)
        }
    }

    fn has_input(&self) -> Result<bool> {
        self.inputs
            .has_samples()
            .map_err(|e| Error::Internal(format!("failed to check for an input: {e}")))
    }

    /// Returns the next input, or [`Error::Again`] if none is available. The input stays valid
    /// until the next poll.
    pub fn poll(&mut self) -> Result<ReadInput<'_>> {
        self.current = None;
        let sample = self
            .inputs
            .receive()
            .map_err(|e| Error::Internal(format!("failed to receive an input: {e}")))?
            .ok_or(Error::Again)?;
        let envelope = sample.user_header();
        let len = usize::try_from(envelope.len)
            .ok()
            .filter(|&len| len <= sample.payload().len())
            .ok_or_else(|| {
                Error::Internal(format!(
                    "input length {} exceeds payload of {} bytes",
                    envelope.len,
                    sample.payload().len()
                ))
            })?;
        let (session, seq) = (envelope.session, envelope.seq);
        let lost = match self.last {
            Some((last_session, last_seq)) if last_session == session && seq > last_seq => {
                seq - last_seq - 1
            }
            _ => 0,
        };
        self.last = Some((session, seq));

        let sample = self.current.insert(sample);
        Ok(ReadInput {
            id: InputId {
                producer: self.producer,
                session,
                seq,
            },
            producer: &self.producer,
            envelope: sample.user_header(),
            data: &sample.payload()[..len],
            lost,
        })
    }
}

impl std::fmt::Debug for Reader {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Reader")
            .field("producer", &self.producer.as_str())
            .field("last", &self.last)
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        Producer,
        test_util::{FLOW, INPUT_LEN, MAX_INPUT_SIZE, TIMEOUT, publish, unique},
    };

    /// A producer, and a reader of its inputs.
    fn fixture() -> (Producer, Reader) {
        let name = unique("camera");
        let producer = Producer::new(&name, Some("image/jpeg"), MAX_INPUT_SIZE).unwrap();
        let reader = Reader::new(&name).unwrap();
        (producer, reader)
    }

    #[test]
    fn published_input_is_read_as_published() {
        let (mut producer, mut reader) = fixture();
        let id = publish(&mut producer, 7);

        reader.wait(TIMEOUT).unwrap();
        let input = reader.poll().unwrap();

        assert_eq!(input.id(), &id);
        assert_eq!(input.flow(), FLOW);
        assert_eq!(input.payload_type(), "image/jpeg");
        assert_eq!(input.data(), [7; INPUT_LEN]);
        assert_eq!(input.lost(), 0);
    }

    #[test]
    fn inputs_are_read_in_order() {
        let (mut producer, mut reader) = fixture();
        publish(&mut producer, 1);
        publish(&mut producer, 2);

        assert_eq!(reader.poll().unwrap().data()[0], 1);
        assert_eq!(reader.poll().unwrap().data()[0], 2);
        assert!(matches!(reader.poll(), Err(Error::Again)));
    }

    #[test]
    fn reader_opened_before_its_producer_reads_its_inputs() {
        let name = unique("camera");
        let mut reader = Reader::new(&name).unwrap();
        let mut producer = Producer::new(&name, None, MAX_INPUT_SIZE).unwrap();
        publish(&mut producer, 3);

        reader.wait(TIMEOUT).unwrap();

        assert_eq!(reader.poll().unwrap().data(), [3; INPUT_LEN]);
    }

    #[test]
    fn inputs_of_a_restarted_producer_are_read_without_loss() {
        let (mut producer, mut reader) = fixture();
        let name = producer_name(&reader);
        publish(&mut producer, 1);
        publish(&mut producer, 2);
        drop(producer);
        let mut restarted = Producer::new(&name, None, MAX_INPUT_SIZE).unwrap();
        publish(&mut restarted, 3);

        let seqs: Vec<_> = std::iter::from_fn(|| {
            let input = reader.poll().ok()?;
            Some((input.id().seq(), input.lost()))
        })
        .collect();

        assert_eq!(seqs.last(), Some(&(1, 0)));
    }

    fn producer_name(reader: &Reader) -> String {
        reader.producer.as_str().to_owned()
    }

    #[test]
    fn wait_without_an_input_times_out() {
        let (_producer, reader) = fixture();

        assert!(matches!(reader.wait(TIMEOUT), Err(Error::Again)));
    }

    #[test]
    fn inputs_lost_while_behind_are_counted() {
        let (mut producer, mut reader) = fixture();
        publish(&mut producer, 1);
        reader.poll().unwrap();
        const BEHIND: u64 = 16;
        for value in 0..BEHIND {
            publish(&mut producer, value as u8);
        }

        let mut read = 0;
        let mut lost = 0;
        let mut last_seq = 0;
        while let Ok(input) = reader.poll() {
            read += 1;
            lost += input.lost();
            last_seq = input.id().seq();
        }

        assert!(lost > 0);
        assert_eq!(read + lost, BEHIND);
        assert_eq!(last_seq, BEHIND + 1);
    }
}
