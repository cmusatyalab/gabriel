//! Client: routes inputs from local producers to a remote server, per flow.
mod receiver;

use crate::Result;

/// Bridges local producers to a remote server.
#[derive(Debug)]
pub struct Client {
    name: String,
    local_server: String,
    remote_server_addr: String,
    receiver: receiver::InputReceiver,
}

impl Client {
    /// Creates a client named `name` that talks to the local server `local_server` and the remote
    /// server at the network address `remote_server_addr`.
    pub fn new(name: &str, local_server: &str, remote_server_addr: &str) -> Result<Self> {
        Ok(Self {
            name: String::from(name),
            local_server: String::from(local_server),
            remote_server_addr: String::from(remote_server_addr),
            receiver: receiver::InputReceiver::new(name)?,
        })
    }

    /// Starts taking inputs from the local producer `name`.
    pub fn add_producer(&mut self, name: &str) -> Result<()> {
        self.receiver.add_producer(name)
    }

    /// Stops taking inputs from the producer `name`.
    pub fn remove_producer(&mut self, name: &str) -> Result<()> {
        self.receiver.remove_producer(name)
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
