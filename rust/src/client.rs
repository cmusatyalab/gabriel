//! Client: routes inputs from local producers to a remote server, per flow.
mod receiver;

use std::{sync::mpsc, thread::JoinHandle};

use crate::{
    client::receiver::{Command, InputReceiver, InputSink},
    Error, Result,
};

/// Bridges local producers to a remote server.
#[derive(Debug)]
pub struct Client {
    name: String,
    local_server: String,
    remote_server_addr: String,
    commands: Option<mpsc::Sender<Command>>,
    receive_thread: Option<JoinHandle<Result<()>>>,
}

impl Client {
    /// Creates a client named `name` that talks to the local server `local_server` and the remote
    /// server at the network address `remote_server_addr`.
    pub fn new(name: &str, local_server: &str, remote_server_addr: &str) -> Result<Self> {
        Self::with_sink(name, local_server, remote_server_addr, Box::new(|_| {}))
    }

    /// Like [`Client::new`], but hands every received input to `sink`. Tests use it to observe
    /// inputs until the network side exists.
    fn with_sink(
        name: &str,
        local_server: &str,
        remote_server_addr: &str,
        sink: InputSink,
    ) -> Result<Self> {
        let (commands, receive_thread) = InputReceiver::spawn(name, sink)?;
        Ok(Self {
            name: String::from(name),
            local_server: String::from(local_server),
            remote_server_addr: String::from(remote_server_addr),
            commands: Some(commands),
            receive_thread: Some(receive_thread),
        })
    }

    /// Starts taking inputs from the local producer `name`.
    pub fn add_producer(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::AddProducer {
            name: name.to_string(),
            reply,
        })
    }

    /// Stops taking inputs from the producer `name`.
    pub fn remove_producer(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::RemoveProducer {
            name: name.to_string(),
            reply,
        })
    }

    /// Sends a command to the receive thread and waits for its reply.
    fn request(&self, command: impl FnOnce(mpsc::Sender<Result<()>>) -> Command) -> Result<()> {
        let (reply, reply_rx) = mpsc::channel();

        let stopped = || Error::Internal(String::from("client receive thread stopped"));
        match self.commands.as_ref() {
            None => return Err(stopped()),
            Some(commands) => commands.send(command(reply)).map_err(|_| stopped())?,
        }
        reply_rx.recv().map_err(|_| stopped())?
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

impl Drop for Client {
    fn drop(&mut self) {
        // Dropping the only command sender disconnects the channel, which makes the receive thread
        // return from its loop. Then wait for it to finish.
        drop(self.commands.take());
        if let Some(thread) = self.receive_thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::mpsc;

    use crate::{
        client::receiver::ReceivedInput,
        test_util::{publish, unique, FLOW, INPUT_LEN, MAX_INPUT_SIZE, TIMEOUT},
        Client, Error, Producer,
    };

    const REMOTE_SERVER_ADDR: &str = "127.0.0.1:4433";

    fn client(input_tx: mpsc::Sender<ReceivedInput>) -> Client {
        Client::with_sink(
            &unique("client"),
            &unique("server"),
            REMOTE_SERVER_ADDR,
            Box::new(move |input| {
                let _ = input_tx.send(input);
            }),
        )
        .unwrap()
    }

    #[test]
    fn added_producer_inputs_reach_sink() {
        let (input_tx, input_rx) = mpsc::channel();
        let producer_name = unique("producer");
        let mut client = client(input_tx);
        client.add_producer(&producer_name).unwrap();

        let mut producer = Producer::new(&producer_name, MAX_INPUT_SIZE).unwrap();
        publish(&mut producer, 7);

        let input = input_rx.recv_timeout(TIMEOUT).unwrap();

        assert_eq!(
            input,
            ReceivedInput {
                producer: producer_name.clone(),
                flow: FLOW.to_string(),
                data: vec![7; INPUT_LEN],
            }
        );
    }

    #[test]
    fn add_and_remove_producer_reject_duplicates_and_unknown_names() {
        let (input_tx, _) = mpsc::channel();
        let producer_name = unique("producer");

        let mut client = client(input_tx);
        client.add_producer(&producer_name).unwrap();
        assert!(matches!(
            client.add_producer(&producer_name),
            Err(Error::InvalidArgument(_))
        ));
        client.remove_producer(&producer_name).unwrap();
        assert!(matches!(
            client.remove_producer(&producer_name),
            Err(Error::InvalidArgument(_))
        ));
    }

    #[test]
    fn client_is_send() {
        fn assert_send<T: Send>() {}
        assert_send::<Client>();
    }
}
