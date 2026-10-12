//! The server's thread. It owns the dispatcher and the local clients' services, because iceoryx2
//! ports cannot move between threads, and takes commands from the [`Server`](super::Server)
//! handle over a channel.
//!
//! The thread sleeps until an engine reports an outcome or a client sends an input, so either is
//! handled right away. It also wakes every [`COMMAND_POLL_INTERVAL`] for commands, control calls
//! and deadlines, and checks every [`ENGINE_CHECK_INTERVAL`] whether an engine's process stopped.
//!
//! Failures the thread carries on after are logged as warnings, with [`tracing`].

use std::{
    net::SocketAddr,
    sync::mpsc::{self, TryRecvError},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use tracing::{error, info_span, warn};

use crate::{
    Error, Result,
    net::Identity,
    server::{
        client_link::{self, ClientLink},
        dispatch::Dispatcher,
        local_clients::LocalClients,
        remote_clients::RemoteClients,
    },
};

/// How long the thread waits for outcomes and inputs before it checks for commands, control calls
/// and deadlines again. Bounds how late a command or a call is answered and an offer times out.
const COMMAND_POLL_INTERVAL: Duration = Duration::from_millis(10);

/// How often the thread checks whether an engine's process stopped. Until then, the inputs
/// delivered to a stopped engine keep its place.
const ENGINE_CHECK_INTERVAL: Duration = Duration::from_secs(1);

pub(super) enum Command {
    AddEngine {
        name: String,
        reply: mpsc::Sender<Result<()>>,
    },
    RemoveEngine {
        name: String,
        reply: mpsc::Sender<Result<()>>,
    },
}

/// Where remote clients reach a server, and the certificate they must trust, in DER.
#[derive(Debug, Clone)]
pub(super) struct RemoteEndpoint {
    pub(super) addr: SocketAddr,
    pub(super) certificate: Vec<u8>,
}

/// A started server thread: its command channel, the thread, and where remote clients reach it.
pub(super) type Spawned = (
    mpsc::Sender<Command>,
    JoinHandle<Result<()>>,
    Option<RemoteEndpoint>,
);

/// Starts the thread of the server `name`, which also accepts remote clients if `remote` is given:
/// on its address, presenting its identity. Returns its command channel once the thread has set
/// up its ports, and where remote clients reach it.
pub(super) fn spawn(name: &str, remote: Option<(SocketAddr, Identity)>) -> Result<Spawned> {
    let (started_tx, started_rx) = mpsc::channel();
    let (command_tx, command_rx) = mpsc::channel();
    let name = name.to_string();
    let thread = thread::Builder::new()
        .name(format!("gabriel-server-{name}"))
        .spawn(move || {
            // Every event the thread logs names the server.
            let _span = info_span!("server", name = %name).entered();
            // Created inside the thread: iceoryx2 ports cannot be moved between threads.
            let opened = Dispatcher::new(&name).and_then(|dispatcher| {
                let mut clients: Vec<Box<dyn ClientLink>> =
                    vec![Box::new(LocalClients::open(dispatcher.node(), &name)?)];
                let mut endpoint = None;
                if let Some((addr, identity)) = remote {
                    let remote_clients =
                        RemoteClients::open(dispatcher.node(), &name, addr, identity)?;
                    endpoint = Some(RemoteEndpoint {
                        addr: remote_clients.local_addr()?,
                        certificate: remote_clients.certificate().to_vec(),
                    });
                    clients.push(Box::new(remote_clients));
                }
                Ok((dispatcher, clients, endpoint))
            });
            let (dispatcher, clients) = match opened {
                Ok((dispatcher, clients, remote)) => {
                    let _ = started_tx.send(Ok(remote));
                    (dispatcher, clients)
                }
                Err(e) => {
                    let _ = started_tx.send(Err(e));
                    return Ok(());
                }
            };
            let stopped = run(dispatcher, clients, &command_rx);
            if let Err(e) = &stopped {
                error!(error = %e, "server thread stopped");
            }
            stopped
        })
        .map_err(|e| Error::Internal(format!("failed to spawn server thread: {e}")))?;
    match started_rx.recv() {
        Ok(Ok(remote)) => Ok((command_tx, thread, remote)),
        Ok(Err(e)) => {
            let _ = thread.join();
            Err(e)
        }
        Err(_) => {
            let _ = thread.join();
            Err(Error::Internal(String::from(
                "server thread exited during startup",
            )))
        }
    }
}

/// Runs until the command channel closes. Returns an error only if the thread cannot wait any
/// more.
fn run(
    mut dispatcher: Dispatcher,
    mut clients: Vec<Box<dyn ClientLink>>,
    commands: &mpsc::Receiver<Command>,
) -> Result<()> {
    let mut next_engine_check = Instant::now();
    loop {
        loop {
            match commands.try_recv() {
                Ok(command) => handle(&mut dispatcher, command),
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => return Ok(()),
            }
        }

        dispatcher.wait(
            clients.iter().map(|link| link.ready()),
            COMMAND_POLL_INTERVAL,
        )?;
        let now = Instant::now();
        // An error here concerns one call, one input or one engine. A failed call was answered
        // with the error, and an input's offer has already ended for the client, or times out
        // there. So the server logs it and carries on. Each step returns its first error only.
        for link in &mut clients {
            if let Err(e) = link.receive(&mut dispatcher, now) {
                warn!(error = %e, "failed to handle a client's call or input");
            }
        }
        if let Err(e) = dispatcher.poll(now) {
            warn!(error = %e, "failed to handle an engine's outcome");
        }
        if now >= next_engine_check {
            if let Err(e) = dispatcher.check_engines(now) {
                warn!(error = %e, "failed to check the engines");
            }
            next_engine_check = now + ENGINE_CHECK_INTERVAL;
        }
        if let Err(e) = client_link::send_events(&mut dispatcher, &mut clients) {
            warn!(error = %e, "failed to send a client its events");
        }
    }
}

fn handle(dispatcher: &mut Dispatcher, command: Command) {
    // A caller that stopped waiting for its reply needs none, so failed replies are ignored.
    match command {
        Command::AddEngine { name, reply } => {
            let _ = reply.send(dispatcher.add_engine(&name));
        }
        Command::RemoveEngine { name, reply } => {
            let _ = reply.send(dispatcher.remove_engine(&name));
        }
    }
}
