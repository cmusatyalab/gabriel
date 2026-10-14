//! Server: runs the flows its clients install, and delivers their inputs to its engines. Its work
//! runs on its own thread.

mod client_link;
mod dispatch;
mod local_clients;
mod remote_clients;
mod worker;

use std::{net::SocketAddr, path::Path, sync::mpsc, thread::JoinHandle};

use crate::{
    Error, Result,
    net::Identity,
    server::worker::{Command, RemoteEndpoint},
};

pub use crate::net::CERTIFICATE_FILE;

/// Delivers the inputs of the flows installed on it to a dynamic set of engines.
#[derive(Debug)]
pub struct Server {
    commands: Option<mpsc::Sender<Command>>,
    thread: Option<JoinHandle<Result<()>>>,
    remote: Option<RemoteEndpoint>,
}

impl Server {
    /// Creates the server `name`. `bind_addr` is the network address to accept remote clients
    /// on, e.g. `0.0.0.0:4433`, or `None` for a local server, which only serves clients on its
    /// machine. Local clients find the server by its name.
    ///
    /// `identity_dir` is an existing directory where a remote server keeps its certificate and
    /// key, so that they stay the same across restarts and its clients can reconnect. The first
    /// time, the server makes them and saves them there. With `None`, the server makes a new certificate each time it
    /// starts. A local server takes no `identity_dir`.
    pub fn new(name: &str, bind_addr: Option<&str>, identity_dir: Option<&Path>) -> Result<Self> {
        let remote = match (bind_addr, identity_dir) {
            (None, None) => None,
            (None, Some(_)) => {
                return Err(Error::InvalidArgument(String::from(
                    "a local server takes no identity directory",
                )));
            }
            (Some(addr), identity_dir) => {
                let addr = addr.parse::<SocketAddr>().map_err(|e| {
                    Error::InvalidArgument(format!("invalid address {addr:?}: {e}"))
                })?;
                let identity = match identity_dir {
                    Some(dir) => Identity::load_or_generate(dir)?,
                    None => Identity::generate()?,
                };
                Some((addr, identity))
            }
        };
        let (commands, thread, remote) = worker::spawn(name, remote)?;
        Ok(Self {
            commands: Some(commands),
            thread: Some(thread),
            remote,
        })
    }

    /// The address remote clients reach the server at, or `None` for a local server. With port 0
    /// in the address the server was created with, this is the port the system picked.
    pub fn remote_addr(&self) -> Option<SocketAddr> {
        self.remote.as_ref().map(|remote| remote.addr)
    }

    /// The certificate remote clients must trust, in DER, or `None` for a local server. It stays
    /// the same across restarts only if the server was created with an identity directory.
    pub fn certificate(&self) -> Option<&[u8]> {
        self.remote
            .as_ref()
            .map(|remote| remote.certificate.as_slice())
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
    use std::os::unix::fs::PermissionsExt;

    use super::*;
    use crate::test_util::unique;

    #[test]
    fn engines_are_added_and_removed_once() {
        let mut server = Server::new(&unique("server"), None, None).unwrap();
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
    fn remote_clients_are_accepted_on_a_picked_port() {
        let server = Server::new(&unique("server"), Some("127.0.0.1:0"), None).unwrap();

        assert_ne!(server.remote_addr().unwrap().port(), 0);
        assert!(!server.certificate().unwrap().is_empty());
    }

    /// A new directory the test owns. Its permissions are set explicitly: iceoryx2 changes the
    /// process's umask while it binds a socket, and parallel tests can leave it changed.
    fn identity_dir() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700)).unwrap();
        dir
    }

    #[test]
    fn identity_is_kept_across_restarts() {
        let dir = identity_dir();
        let identity_dir = dir.path();
        let first = Server::new(&unique("server"), Some("127.0.0.1:0"), Some(identity_dir))
            .unwrap()
            .certificate()
            .unwrap()
            .to_vec();

        let second = Server::new(&unique("server"), Some("127.0.0.1:0"), Some(identity_dir))
            .unwrap()
            .certificate()
            .unwrap()
            .to_vec();

        assert_eq!(first, second);
        let key = std::fs::metadata(identity_dir.join("key.der")).unwrap();
        assert_eq!(key.permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn part_of_an_identity_is_rejected() {
        let dir = identity_dir();
        Server::new(&unique("server"), Some("127.0.0.1:0"), Some(dir.path())).unwrap();
        std::fs::remove_file(dir.path().join("key.der")).unwrap();

        let result = Server::new(&unique("server"), Some("127.0.0.1:0"), Some(dir.path()));

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn local_server_takes_no_identity() {
        let dir = identity_dir();

        let result = Server::new(&unique("server"), None, Some(dir.path()));

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn invalid_address_is_rejected() {
        let result = Server::new(&unique("server"), Some("not an address"), None);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn server_is_send() {
        fn assert_send<T: Send>() {}
        assert_send::<Server>();
    }
}
