use crate::{
    Error, Result,
    envelope::Envelope,
    services::{self, InputPayload},
};
use iceoryx2::{
    port::{listener::Listener, subscriber::Subscriber},
    prelude::*,
};
use std::{
    collections::HashMap,
    sync::mpsc::{self, TryRecvError},
    thread::{self, JoinHandle},
    time::Duration,
};

#[derive(Debug)]
pub(super) struct InputReceiver {
    node: Node<ipc::Service>,
    producers: HashMap<String, ProducerPorts>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct ReceivedInput {
    pub(super) producer: String,
    pub(super) flow: String,
    pub(super) data: Vec<u8>,
}

pub(super) enum Command {
    AddProducer {
        name: String,
        reply: mpsc::Sender<Result<()>>,
    },
    RemoveProducer {
        name: String,
        reply: mpsc::Sender<Result<()>>,
    },
}

pub(super) type InputSink = Box<dyn FnMut(ReceivedInput) + Send>;

/// How long to wait for input before checking for commands again. Bounds how long
/// add_producer, remove_producer and shutdown can take.
const COMMAND_POLL_INTERVAL: Duration = Duration::from_millis(10);

impl InputReceiver {
    pub(super) fn new(name: &str) -> Result<Self> {
        let node_name = NodeName::new(name).map_err(|e| Error::InvalidArgument(e.to_string()))?;
        let node = NodeBuilder::new()
            .name(&node_name)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        Ok(Self {
            node,
            producers: HashMap::new(),
        })
    }

    pub(super) fn spawn(
        name: &str,
        sink: InputSink,
    ) -> Result<(mpsc::Sender<Command>, JoinHandle<Result<()>>)> {
        if name.is_empty() {
            return Err(Error::InvalidArgument(String::from("name cannot be empty")));
        }
        let (started_tx, started_rx) = mpsc::channel();
        let (command_tx, command_rx) = mpsc::channel();
        let name = name.to_string();
        let thread = thread::Builder::new()
            .name(format!("gabriel-client-{name}"))
            .spawn(move || {
                // Created inside the thread: ipc ports can't be moved between threads.
                let receiver = match InputReceiver::new(&name) {
                    Ok(receiver) => {
                        let _ = started_tx.send(Ok(()));
                        receiver
                    }
                    Err(e) => {
                        let _ = started_tx.send(Err(e));
                        return Ok(());
                    }
                };
                receiver.run(&command_rx, sink)
            })
            .map_err(|e| Error::Internal(format!("failed to spawn receive thread: {e}")))?;
        match started_rx.recv() {
            Ok(Ok(())) => Ok((command_tx, thread)),
            Ok(Err(e)) => {
                let _ = thread.join();
                Err(e)
            }
            Err(_) => {
                let _ = thread.join();
                Err(Error::Internal(String::from(
                    "receive thread exited during startup",
                )))
            }
        }
    }

    fn run(mut self, commands: &mpsc::Receiver<Command>, mut sink: InputSink) -> Result<()> {
        loop {
            loop {
                let command = if self.producers.is_empty() {
                    match commands.recv() {
                        Ok(command) => command,
                        Err(_) => return Ok(()),
                    }
                } else {
                    match commands.try_recv() {
                        Ok(command) => command,
                        Err(TryRecvError::Empty) => break,
                        Err(TryRecvError::Disconnected) => return Ok(()),
                    }
                };
                self.handle(command);
            }
            for input in self.wait_for_input(COMMAND_POLL_INTERVAL)? {
                sink(input);
            }
        }
    }

    /// Starts taking inputs from the local producer `name`.
    pub(super) fn add_producer(&mut self, name: &str) -> Result<()> {
        if self.producers.contains_key(name) {
            return Err(Error::InvalidArgument(format!(
                "producer {name} already exists"
            )));
        }

        let subscriber = services::open_producer_input(&self.node, name)?
            .subscriber_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create subscriber: {e}")))?;

        let listener = services::open_producer_ready(&self.node, name)?
            .listener_builder()
            .create()
            .map_err(|e| Error::Internal(format!("failed to create listener: {e}")))?;

        let producer_ports = ProducerPorts {
            subscriber,
            listener,
        };

        self.producers.insert(String::from(name), producer_ports);
        Ok(())
    }

    /// Stops taking inputs from the producer `name`.
    pub(super) fn remove_producer(&mut self, name: &str) -> Result<()> {
        match self.producers.remove(name) {
            Some(_) => Ok(()),
            None => Err(Error::InvalidArgument(format!(
                "producer {name} does not exist"
            ))),
        }
    }

    /// Waits up to `timeout` for producers to publish, returning the newest
    /// input from each producer that did. An empty result means the wait timed out.
    fn wait_for_input(&self, timeout: Duration) -> Result<Vec<ReceivedInput>> {
        if self.producers.is_empty() {
            return Ok(Vec::new());
        }
        // Gabriel is a library: leave SIGINT/SIGTERM handling to the host application.
        let waitset = WaitSetBuilder::new()
            .signal_handling_mode(SignalHandlingMode::Disabled)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create waitset: {e}")))?;

        let mut attachment_id_to_ports = HashMap::new();
        let mut guards = Vec::new();

        // Attach the producer listeners to a waitset that lets us wait on them together. Each
        // attachment returns a guard whose lifetime defines the duration for which the listener is
        // attached to the waitset, so each guard is added to a vector.
        for (producer_name, ports) in &self.producers {
            let guard = waitset.attach_notification(&ports.listener).map_err(|e| {
                Error::Internal(format!(
                    "failed to attach producer {producer_name:?} to waitset: {e}"
                ))
            })?;
            attachment_id_to_ports.insert(
                WaitSetAttachmentId::from_guard(&guard),
                (producer_name, ports),
            );
            guards.push(guard);
        }

        let mut inputs = Vec::new();
        let mut error = None;

        // A closure that first determines which producer's listener fired, and then collects the
        // latest input for that producer.
        let on_event = |id: WaitSetAttachmentId<ipc::Service>| {
            if let Some((producer_name, ports)) = attachment_id_to_ports.get(&id) {
                match ports.drain_newest() {
                    Ok(Some((flow, data))) => inputs.push(ReceivedInput {
                        producer: producer_name.to_string(),
                        flow,
                        data,
                    }),
                    Ok(None) => (),
                    Err(e) => {
                        error = Some(e);
                        return CallbackProgression::Stop;
                    }
                }
            }
            CallbackProgression::Continue
        };
        waitset
            .wait_and_process_once_with_timeout(on_event, timeout)
            .map_err(|e| Error::Internal(format!("failed to wait for input: {e}")))?;

        match error {
            Some(e) => Err(e),
            None => Ok(inputs),
        }
    }

    fn handle(&mut self, command: Command) {
        let _ = match command {
            Command::AddProducer { name, reply } => reply.send(self.add_producer(&name)),
            Command::RemoveProducer { name, reply } => reply.send(self.remove_producer(&name)),
        };
    }
}

#[derive(Debug)]
struct ProducerPorts {
    subscriber: Subscriber<ipc::Service, InputPayload, Envelope>,
    listener: Listener<ipc::Service>,
}

impl ProducerPorts {
    /// Consumes every pending notification and input, returning a copy of the newest input (older
    /// ones are stale and dropped).
    fn drain_newest(&self) -> Result<Option<(String, Vec<u8>)>> {
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

        Ok(Some((header.flow.to_string(), data.to_vec())))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        Producer,
        test_util::{FLOW, INPUT_LEN, MAX_INPUT_SIZE, TIMEOUT, publish, unique},
    };

    fn receiver() -> InputReceiver {
        InputReceiver::new(&unique("client")).unwrap()
    }

    #[test]
    fn wait_without_producers_returns_nothing() {
        assert!(receiver().wait_for_input(TIMEOUT).unwrap().is_empty());
    }

    #[test]
    fn wait_times_out_when_nothing_is_published() {
        let name = unique("producer");
        let _producer = Producer::new(&name, MAX_INPUT_SIZE).unwrap();
        let mut receiver = receiver();
        receiver.add_producer(&name).unwrap();

        assert!(receiver.wait_for_input(TIMEOUT).unwrap().is_empty());
    }

    #[test]
    fn wait_returns_newest_input_of_each_producer() {
        let (name_a, name_b) = (unique("producer-a"), unique("producer-b"));
        let mut producer_a = Producer::new(&name_a, MAX_INPUT_SIZE).unwrap();
        let mut producer_b = Producer::new(&name_b, MAX_INPUT_SIZE).unwrap();
        let mut receiver = receiver();
        receiver.add_producer(&name_a).unwrap();
        receiver.add_producer(&name_b).unwrap();

        publish(&mut producer_a, 1);
        publish(&mut producer_a, 2);
        publish(&mut producer_b, 9);

        let mut inputs = receiver.wait_for_input(TIMEOUT).unwrap();
        inputs.sort_by(|a, b| a.producer.cmp(&b.producer));
        let mut expected = vec![
            ReceivedInput {
                producer: name_a,
                flow: FLOW.to_string(),
                data: vec![2; INPUT_LEN],
            },
            ReceivedInput {
                producer: name_b,
                flow: FLOW.to_string(),
                data: vec![9; INPUT_LEN],
            },
        ];
        expected.sort_by(|a, b| a.producer.cmp(&b.producer));
        assert_eq!(inputs, expected);
    }

    #[test]
    fn drained_inputs_are_not_returned_again() {
        let name = unique("producer");
        let mut producer = Producer::new(&name, MAX_INPUT_SIZE).unwrap();
        let mut receiver = receiver();
        receiver.add_producer(&name).unwrap();

        publish(&mut producer, 1);
        assert_eq!(receiver.wait_for_input(TIMEOUT).unwrap().len(), 1);
        assert!(receiver.wait_for_input(TIMEOUT).unwrap().is_empty());
    }

    #[test]
    fn add_and_remove_producer_reject_duplicates_and_unknown_names() {
        let name = unique("producer");
        let mut receiver = receiver();

        receiver.add_producer(&name).unwrap();
        assert!(matches!(
            receiver.add_producer(&name),
            Err(Error::InvalidArgument(_))
        ));
        receiver.remove_producer(&name).unwrap();
        assert!(matches!(
            receiver.remove_producer(&name),
            Err(Error::InvalidArgument(_))
        ));
    }

    #[test]
    fn spawned_receiver_delivers_inputs_and_stops_when_dropped() {
        let (input_tx, input_rx) = mpsc::channel();
        let client_name = unique("client");
        let (commands, thread) = InputReceiver::spawn(
            &client_name,
            Box::new(move |input| {
                let _ = input_tx.send(input);
            }),
        )
        .unwrap();

        let producer_name = unique("producer");
        let mut producer = Producer::new(&producer_name, MAX_INPUT_SIZE).unwrap();
        let (reply, reply_rx) = mpsc::channel();
        commands
            .send(Command::AddProducer {
                name: producer_name.clone(),
                reply,
            })
            .unwrap();
        reply_rx.recv().unwrap().unwrap();

        publish(&mut producer, 7);
        let input = input_rx.recv_timeout(TIMEOUT).unwrap();
        assert_eq!(input.data, vec![7; INPUT_LEN]);

        drop(commands); // disconnect → run returns
        thread.join().unwrap().unwrap();
    }

    #[test]
    fn spawn_reports_startup_error() {
        let result = InputReceiver::spawn("", Box::new(|_| {}));
        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }
}
