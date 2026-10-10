//! The client's ends of its producers' services. It keeps only the newest input of each producer,
//! and publishes the events about each producer's inputs, for the producer's app.

use std::{collections::HashMap, sync::Arc};

use iceoryx2::{
    port::{listener::Listener, notifier::Notifier, publisher::Publisher, subscriber::Subscriber},
    prelude::*,
};

use crate::{
    Error, Result,
    envelope::Envelope,
    ipc::types::{EventRecord, FlowName},
    routing::ids::{InputId, ProducerName},
    services::{self, EventPayload, InputPayload},
};

/// The slice length a result publisher starts with. It grows as needed.
const INITIAL_SLICE_LEN: usize = 4096;

#[derive(Debug, Default)]
pub(super) struct Producers {
    producers: HashMap<ProducerName, ProducerPorts>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ReceivedInput {
    /// The id the producer gave the input.
    pub(super) id: InputId,
    /// The flow the producer published the input on.
    pub(super) flow: FlowName,
    pub(super) data: Arc<[u8]>,
}

impl Producers {
    /// Starts taking inputs from the local producer `name`.
    pub(super) fn add_producer(&mut self, node: &Node<ipc::Service>, name: &str) -> Result<()> {
        let producer = ProducerName::try_from(name)
            .map_err(|_| Error::InvalidArgument(format!("producer name {name:?} is too long")))?;
        if self.producers.contains_key(&producer) {
            return Err(Error::InvalidArgument(format!(
                "producer {name} already exists"
            )));
        }

        let subscriber = services::open_producer_input(node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create subscriber: {e}")))?;

        let listener = services::open_producer_ready(node, name)?
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create listener: {e}")))?;

        let results = services::open_producer_results(node, name)?
            .publisher_builder()
            .initial_max_slice_len(INITIAL_SLICE_LEN)
            .allocation_strategy(AllocationStrategy::PowerOfTwo)
            .create()
            .map_err(|e| Error::Internal(format!("failed to create result publisher: {e}")))?;

        let results_ready = services::open_producer_results_ready(node, name)?
            .notifier_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create result notifier: {e}")))?;

        let producer_ports = ProducerPorts {
            subscriber,
            listener,
            results,
            results_ready,
            next_result: 1,
            unannounced: false,
        };

        self.producers.insert(producer, producer_ports);
        Ok(())
    }

    /// Stops taking inputs from the producer `name`.
    pub(super) fn remove_producer(&mut self, name: &str) -> Result<()> {
        let removed = ProducerName::try_from(name)
            .ok()
            .and_then(|producer| self.producers.remove(&producer));
        match removed {
            Some(_) => Ok(()),
            None => Err(Error::InvalidArgument(format!(
                "producer {name} does not exist"
            ))),
        }
    }

    /// Whether inputs are taken from the producer `name`.
    pub(super) fn has_producer(&self, name: &ProducerName) -> bool {
        self.producers.contains_key(name)
    }

    /// The listeners that wake the client when a producer publishes.
    pub(super) fn listeners(&self) -> impl Iterator<Item = &Listener<ipc::Service>> {
        self.producers.values().map(|ports| &ports.listener)
    }

    /// Numbers `record`, an event about an input of the producer `producer`, and publishes it with
    /// `result` on the producer's result service. Does nothing for a producer that was removed.
    pub(super) fn publish(
        &mut self,
        producer: &ProducerName,
        record: EventRecord,
        result: &[u8],
    ) -> Result<()> {
        let Some(ports) = self.producers.get_mut(producer) else {
            return Ok(());
        };
        let mut sample = ports
            .results
            .loan_slice_uninit(result.len())
            .map_err(|e| Error::Internal(format!("failed to loan event: {e}")))?;
        *sample.user_header_mut() = record.numbered(ports.next_result);
        ports.next_result += 1;
        sample
            .write_from_slice(result)
            .send()
            .map_err(|e| Error::Internal(format!("failed to publish event: {e}")))?;
        ports.unannounced = true;
        Ok(())
    }

    /// Wakes the subscribers of every producer with events published since the last call.
    /// Returns the first error, after waking the rest.
    pub(super) fn wake_subscribers(&mut self) -> Result<()> {
        let mut first_error = None;
        for ports in self
            .producers
            .values_mut()
            .filter(|ports| ports.unannounced)
        {
            ports.unannounced = false;
            if let Err(e) = ports.results_ready.notify() {
                first_error.get_or_insert(Error::Internal(format!(
                    "failed to wake result subscribers: {e}"
                )));
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    /// Returns the newest input of each producer that published since the last call. Older inputs
    /// are stale and dropped. An input that cannot be read is dropped too; the others are still
    /// returned.
    pub(super) fn take_inputs(&self) -> Vec<ReceivedInput> {
        self.producers
            .iter()
            .filter_map(|(producer, ports)| ports.drain_newest(producer).ok().flatten())
            .collect()
    }
}

#[derive(Debug)]
struct ProducerPorts {
    subscriber: Subscriber<ipc::Service, InputPayload, Envelope>,
    listener: Listener<ipc::Service>,
    /// The events about the producer's inputs, for its app.
    results: Publisher<ipc::Service, EventPayload, EventRecord>,
    /// Wakes the subscribers of `results` after each batch of events.
    results_ready: Notifier<ipc::Service>,
    /// The sequence number of the next event on `results`.
    next_result: u64,
    /// Whether events were published since the subscribers were last woken.
    unannounced: bool,
}

impl ProducerPorts {
    /// Consumes every pending notification and input of `producer`, returning a copy of the newest
    /// input (older ones are stale and dropped).
    fn drain_newest(&self, producer: &ProducerName) -> Result<Option<ReceivedInput>> {
        // Without this the waitset keeps reporting the same notifications.
        self.listener
            .try_wait(|_| {})
            .map_err(|e| Error::Internal(format!("failed to drain ready event: {e}")))?;

        let mut newest = None;
        while let Some(sample) = self
            .subscriber
            .receive()
            .map_err(|e| Error::Internal(format!("failed to receive input: {e}")))?
        {
            newest = Some(sample);
        }
        let Some(sample) = newest else {
            return Ok(None);
        };
        let header = sample.user_header();
        let payload = sample.payload();
        let len = usize::try_from(header.len)
            .map_err(|_| Error::Internal(String::from("failed to parse payload length")))?;
        let data = payload.get(..len).ok_or_else(|| {
            Error::Internal(format!(
                "input length {} exceeds payload of {} bytes",
                header.len,
                payload.len()
            ))
        })?;

        Ok(Some(ReceivedInput {
            id: InputId {
                producer: *producer,
                session: header.session,
                seq: header.seq,
            },
            flow: header.flow,
            data: Arc::from(data),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        Producer,
        test_util::{FLOW, INPUT_LEN, MAX_INPUT_SIZE, TIMEOUT, publish, unique},
    };

    fn node() -> Node<ipc::Service> {
        NodeBuilder::new().create::<ipc::Service>().unwrap()
    }

    /// Waits for a producer to publish, then takes the inputs.
    fn wait_for_inputs(producers: &Producers) -> Vec<ReceivedInput> {
        services::wait_for_any(producers.listeners(), TIMEOUT).unwrap();
        producers.take_inputs()
    }

    #[test]
    fn nothing_is_taken_when_nothing_is_published() {
        let name = unique("producer");
        let _producer = Producer::new(&name, MAX_INPUT_SIZE).unwrap();
        let mut producers = Producers::default();
        producers.add_producer(&node(), &name).unwrap();

        assert!(wait_for_inputs(&producers).is_empty());
    }

    #[test]
    fn newest_input_of_each_producer_is_taken() {
        let (name_a, name_b) = (unique("producer-a"), unique("producer-b"));
        let mut producer_a = Producer::new(&name_a, MAX_INPUT_SIZE).unwrap();
        let mut producer_b = Producer::new(&name_b, MAX_INPUT_SIZE).unwrap();
        let node = node();
        let mut producers = Producers::default();
        producers.add_producer(&node, &name_a).unwrap();
        producers.add_producer(&node, &name_b).unwrap();

        publish(&mut producer_a, 1);
        publish(&mut producer_a, 2);
        publish(&mut producer_b, 9);

        // Sessions are random, so compare everything else: producer, sequence number, flow, data.
        let mut inputs: Vec<_> = wait_for_inputs(&producers)
            .into_iter()
            .map(|input| {
                (
                    input.id.producer.to_string(),
                    input.id.seq,
                    input.flow.to_string(),
                    input.data.to_vec(),
                )
            })
            .collect();
        inputs.sort();
        let mut expected = vec![
            (name_a, 2, FLOW.to_string(), vec![2; INPUT_LEN]),
            (name_b, 1, FLOW.to_string(), vec![9; INPUT_LEN]),
        ];
        expected.sort();
        assert_eq!(inputs, expected);
    }

    #[test]
    fn taken_inputs_are_not_taken_again() {
        let name = unique("producer");
        let mut producer = Producer::new(&name, MAX_INPUT_SIZE).unwrap();
        let mut producers = Producers::default();
        producers.add_producer(&node(), &name).unwrap();

        publish(&mut producer, 1);
        assert_eq!(wait_for_inputs(&producers).len(), 1);
        assert!(wait_for_inputs(&producers).is_empty());
    }

    #[test]
    fn add_and_remove_producer_reject_duplicates_and_unknown_names() {
        let name = unique("producer");
        let node = node();
        let mut producers = Producers::default();

        producers.add_producer(&node, &name).unwrap();
        assert!(matches!(
            producers.add_producer(&node, &name),
            Err(Error::InvalidArgument(_))
        ));
        producers.remove_producer(&name).unwrap();
        assert!(matches!(
            producers.remove_producer(&name),
            Err(Error::InvalidArgument(_))
        ));
    }
}
