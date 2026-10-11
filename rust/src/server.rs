//! Server: runs the flows its clients install, and delivers their inputs to its engines. Its work
//! runs on its own thread.

mod client_link;
mod dispatch;
mod local_clients;
mod worker;

use std::{sync::mpsc, thread::JoinHandle};

use crate::{Error, Result, server::worker::Command};

/// Delivers the inputs of the flows installed on it to a dynamic set of engines.
#[derive(Debug)]
pub struct Server {
    commands: Option<mpsc::Sender<Command>>,
    thread: Option<JoinHandle<Result<()>>>,
}

impl Server {
    /// Creates the server `name`. `bind_addr` is the network address to accept remote clients
    /// on, or `None` for a local server, which only serves clients on its machine. Local clients
    /// find the server by its name.
    pub fn new(name: &str, bind_addr: Option<&str>) -> Result<Self> {
        if let Some(addr) = bind_addr {
            return Err(Error::InvalidArgument(format!(
                "cannot accept remote clients on {addr:?}: not supported yet"
            )));
        }
        let (commands, thread) = worker::spawn(name)?;
        Ok(Self {
            commands: Some(commands),
            thread: Some(thread),
        })
    }

    /// Starts serving the local engine `name`. The engine may start before or after this call.
    pub fn add_engine(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::AddEngine {
            name: name.to_string(),
            reply,
        })
    }

    /// Stops serving the engine `name`. Fails while an installed flow uses it.
    pub fn remove_engine(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::RemoveEngine {
            name: name.to_string(),
            reply,
        })
    }

    /// Sends a command to the server thread and waits for its reply.
    fn request(&self, command: impl FnOnce(mpsc::Sender<Result<()>>) -> Command) -> Result<()> {
        let (reply, reply_rx) = mpsc::channel();
        let stopped = || Error::Internal(String::from("server thread stopped"));
        match self.commands.as_ref() {
            None => return Err(stopped()),
            Some(commands) => commands.send(command(reply)).map_err(|_| stopped())?,
        }
        reply_rx.recv().map_err(|_| stopped())?
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        // Dropping the only command sender disconnects the channel, which makes the server thread
        // return from its loop. Then wait for it to finish.
        drop(self.commands.take());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_util::unique;

    #[test]
    fn engines_are_added_and_removed_once() {
        let mut server = Server::new(&unique("server"), None).unwrap();
        let name = unique("engine");

        server.add_engine(&name).unwrap();
        assert!(matches!(
            server.add_engine(&name),
            Err(Error::InvalidArgument(_))
        ));
        server.remove_engine(&name).unwrap();
        assert!(matches!(
            server.remove_engine(&name),
            Err(Error::InvalidArgument(_))
        ));
    }

    #[test]
    fn remote_clients_are_not_supported_yet() {
        let result = Server::new(&unique("server"), Some("127.0.0.1:4433"));

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn server_is_send() {
        fn assert_send<T: Send>() {}
        assert_send::<Server>();
    }
}
