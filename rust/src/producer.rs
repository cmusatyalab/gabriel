//! Producer: publishes inputs into shared memory for engines to consume.

use crate::envelope::{Envelope, MAX_FLOW_NAME};
use crate::{Error, Result, services};
use iceoryx2::port::notifier::Notifier;
use iceoryx2::port::publisher::Publisher;
use iceoryx2::prelude::*;
use iceoryx2::sample_mut_uninit::SampleMutUninit;
use iceoryx2_bb_container::string::StaticString;
use std::fmt;
use std::mem::MaybeUninit;
use uuid::Uuid;

/// Publishes inputs. An input is written in place into a loaned shared memory buffer
/// ([`Producer::new_input`]) and then sent ([`Producer::publish_input`]).
pub struct Producer {
    id: String,
    max_input_size: usize,
    node: Node<ipc::Service>,
    publisher: Publisher<ipc::Service, [u8], Envelope>,
    notifier: Notifier<ipc::Service>,
    pending: Option<SampleMutUninit<ipc::Service, [MaybeUninit<u8>], Envelope>>,
}

impl Producer {
    /// Creates the producer `name` whose inputs are at most `max_input_size` bytes.
    pub fn new(name: &str, max_input_size: usize) -> Result<Self> {
        let node_name = NodeName::new(name).map_err(|e| Error::InvalidArgument(e.to_string()))?;
        let node = NodeBuilder::new()
            .name(&node_name)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;

        let publisher = services::open_producer_input(&node, name)?
            .publisher_builder()
            .max_loaned_samples(1)
            .initial_max_slice_len(max_input_size)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create publisher: {e}")))?;

        let notifier = services::open_producer_ready(&node, name)?
            .notifier_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create notifier: {e}")))?;

        let id = Uuid::new_v4();
        Ok(Self {
            id: format!("{name}-{id}"),
            max_input_size,
            node,
            publisher,
            notifier,
            pending: None,
        })
    }

    /// Loans a buffer of `max_input_size` bytes for the next input.
    pub fn new_input(&mut self) -> Result<&mut [MaybeUninit<u8>]> {
        self.pending = None;
        let sample = self
            .publisher
            .loan_slice_uninit(self.max_input_size)
            .map_err(|e| Error::Internal(e.to_string()))?;
        Ok(self.pending.insert(sample).payload_mut())
    }

    /// Publishes the first `len` bytes of the loaned buffer on `flow`.
    pub fn publish_input(&mut self, len: usize, flow: &str) -> Result<()> {
        if len > self.max_input_size {
            return Err(Error::SizeExceeded {
                size: len,
                max: self.max_input_size,
            });
        }
        let flow = StaticString::<MAX_FLOW_NAME>::try_from(flow)
            .map_err(|e| Error::InvalidArgument(format!("invalid flow name {flow:?}: {e}")))?;

        let mut sample = self.pending.take().ok_or_else(|| {
            Error::InvalidArgument(String::from("no pending input; call new_input first"))
        })?;
        *sample.user_header_mut() = Envelope {
            len: len as u64,
            flow,
        };

        // SAFETY: the caller has written the first `len` bytes. Subscribers
        // only read `..envelope.len`, so the uninitialized tail is never read.
        let sample = unsafe { sample.assume_init() };
        sample
            .send()
            .map_err(|e| Error::Internal(format!("failed to send input: {e}")))?;

        self.notifier
            .notify()
            .map_err(|e| Error::Internal(format!("failed to notify: {e}")))?;

        Ok(())
    }
}

impl fmt::Debug for Producer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Producer")
            .field("node", &self.node.name())
            .field("max_input_size", &self.max_input_size)
            .field("pending", &self.pending.is_some())
            .finish_non_exhaustive()
    }
}
