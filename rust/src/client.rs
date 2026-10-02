//! Client: routes inputs from local producers to a remote server, per flow.

use std::{collections::HashMap, time::Duration};

use crate::{
    envelope::Envelope,
    services::{self, InputPayload},
    Error, Result,
};
use iceoryx2::{
    port::{listener::Listener, subscriber::Subscriber},
    prelude::*,
};

/// Bridges local producers to a remote server.
#[derive(Debug)]
pub struct Client {
    name: String,
    local_server: String,
    remote_server_addr: String,
    node: Node<ipc::Service>,
    producers: HashMap<String, ProducerPorts>,
}

#[derive(Debug)]
struct ProducerPorts {
    subscriber: Subscriber<ipc::Service, InputPayload, Envelope>,
    listener: Listener<ipc::Service>,
}

impl Client {
    /// Creates a client named `name` that talks to the local server
    /// `local_server` and the remote server at the network address
    /// `remote_server_addr`.
    pub fn new(name: &str, local_server: &str, remote_server_addr: &str) -> Result<Self> {
        let node_name = NodeName::new(name).map_err(|e| Error::InvalidArgument(e.to_string()))?;
        let node = NodeBuilder::new()
            .name(&node_name)
            .create::<ipc::Service>()
            .map_err(|e| Error::Internal(format!("failed to create node: {e}")))?;
        Ok(Self {
            name: String::from(name),
            local_server: String::from(local_server),
            remote_server_addr: String::from(remote_server_addr),
            node,
            producers: HashMap::new(),
        })
    }

    /// Starts taking inputs from the local producer `name`.
    pub fn add_producer(&mut self, name: &str) -> Result<()> {
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
    pub fn remove_producer(&mut self, name: &str) -> Result<()> {
        match self.producers.remove(name) {
            Some(_) => Ok(()),
            None => Err(Error::InvalidArgument(format!(
                "producer {name} does not exist"
            ))),
        }
    }

    /// Waits up to `timeout` for producers to publish, returning the newest
    /// input from each producer that did. An empty result means the wait timed out.
    fn wait_for_input(&self, timeout: Duration) -> Result<Vec<(String, Vec<u8>)>> {
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
        for (name, ports) in &self.producers {
            let guard = waitset.attach_notification(&ports.listener).map_err(|e| {
                Error::Internal(format!(
                    "failed to attach producer {name:?} to waitset: {e}"
                ))
            })?;
            attachment_id_to_ports.insert(WaitSetAttachmentId::from_guard(&guard), (name, ports));
            guards.push(guard);
        }

        let mut inputs = Vec::new();
        let mut error = None;

        // A closure that first determines which producer's listener fired, and then collects the
        // latest input for that producer.
        let on_event = |id: WaitSetAttachmentId<ipc::Service>| {
            if let Some((name, ports)) = attachment_id_to_ports.get(&id) {
                match ports.drain_newest() {
                    Ok(Some(input)) => inputs.push((name.to_string(), input)),
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

    /// Adds the flow `name` described by `spec`.
    pub fn add_flow(&mut self, name: &str, spec: &str) -> Result<()> {
        let _ = (name, spec);
        todo!()
    }

    /// Removes the flow `name`.
    pub fn remove_flow(&mut self, name: &str) -> Result<()> {
        let _ = name;
        todo!()
    }
}

impl ProducerPorts {
    /// Consumes every pending notification and input, returning a copy of the newest input (older
    /// ones are stale and dropped).
    fn drain_newest(&self) -> Result<Option<Vec<u8>>> {
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

        newest
            .map(|sample| {
                // The envelope comes from another process, so check it before trusting it.
                let payload = sample.payload();
                usize::try_from(sample.user_header().len)
                    .ok()
                    .and_then(|len| payload.get(..len))
                    .map(<[u8]>::to_vec)
                    .ok_or_else(|| {
                        Error::Internal(format!(
                            "input length {} exceeds payload of {} bytes",
                            sample.user_header().len,
                            payload.len()
                        ))
                    })
            })
            .transpose()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::Producer;
    use uuid::Uuid;

    /// Long enough for a published input to arrive, short enough to keep tests fast.
    const TIMEOUT: Duration = Duration::from_millis(200);
    const MAX_INPUT_SIZE: usize = 16;
    const INPUT_LEN: usize = 4;
    const FLOW: &str = "flow";
    /// Unused by these tests, which never reach the network.
    const REMOTE_SERVER_ADDR: &str = "tcp://127.0.0.1:0";

    /// iceoryx2 services are shared machine-wide, so every test uses fresh
    /// names to stay isolated from parallel tests and other test runs.
    fn unique(prefix: &str) -> String {
        format!("{prefix}-{}", Uuid::new_v4())
    }

    fn client() -> Client {
        Client::new(&unique("client"), &unique("server"), REMOTE_SERVER_ADDR).unwrap()
    }

    /// Publishes an input of `INPUT_LEN` bytes, all set to `value`.
    fn publish(producer: &mut Producer, value: u8) {
        let buf = producer.new_input().unwrap();
        for byte in &mut buf[..INPUT_LEN] {
            byte.write(value);
        }
        producer.publish_input(INPUT_LEN, FLOW).unwrap();
    }

    #[test]
    fn wait_without_producers_returns_nothing() {
        assert!(client().wait_for_input(TIMEOUT).unwrap().is_empty());
    }

    #[test]
    fn wait_times_out_when_nothing_is_published() {
        let name = unique("producer");
        let _producer = Producer::new(&name, MAX_INPUT_SIZE).unwrap();
        let mut client = client();
        client.add_producer(&name).unwrap();

        assert!(client.wait_for_input(TIMEOUT).unwrap().is_empty());
    }

    #[test]
    fn wait_returns_newest_input_of_each_producer() {
        let (name_a, name_b) = (unique("producer-a"), unique("producer-b"));
        let mut producer_a = Producer::new(&name_a, MAX_INPUT_SIZE).unwrap();
        let mut producer_b = Producer::new(&name_b, MAX_INPUT_SIZE).unwrap();
        let mut client = client();
        client.add_producer(&name_a).unwrap();
        client.add_producer(&name_b).unwrap();

        publish(&mut producer_a, 1);
        publish(&mut producer_a, 2);
        publish(&mut producer_b, 9);

        let mut inputs = client.wait_for_input(TIMEOUT).unwrap();
        inputs.sort();
        let mut expected = vec![
            (name_a, vec![2; INPUT_LEN]),
            (name_b, vec![9; INPUT_LEN]),
        ];
        expected.sort();
        assert_eq!(inputs, expected);
    }

    #[test]
    fn drained_inputs_are_not_returned_again() {
        let name = unique("producer");
        let mut producer = Producer::new(&name, MAX_INPUT_SIZE).unwrap();
        let mut client = client();
        client.add_producer(&name).unwrap();

        publish(&mut producer, 1);
        assert_eq!(client.wait_for_input(TIMEOUT).unwrap().len(), 1);
        assert!(client.wait_for_input(TIMEOUT).unwrap().is_empty());
    }

    #[test]
    fn add_and_remove_producer_reject_duplicates_and_unknown_names() {
        let name = unique("producer");
        let mut client = client();

        client.add_producer(&name).unwrap();
        assert!(matches!(client.add_producer(&name), Err(Error::InvalidArgument(_))));
        client.remove_producer(&name).unwrap();
        assert!(matches!(client.remove_producer(&name), Err(Error::InvalidArgument(_))));
    }
}
