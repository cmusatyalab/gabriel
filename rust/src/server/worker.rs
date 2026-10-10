//! The server's thread. It owns the dispatcher and the local clients' services, because iceoryx2
//! ports cannot move between threads, and takes commands from the [`Server`](super::Server)
//! handle over a channel.
//!
//! The thread sleeps until an engine reports an outcome or a client sends an input, so either is
//! handled right away. It also wakes every [`COMMAND_POLL_INTERVAL`] for commands, control calls
//! and deadlines, and checks every [`ENGINE_CHECK_INTERVAL`] whether an engine's process stopped.

use std::{
    sync::mpsc::{self, TryRecvError},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use crate::{
    Error, Result,
    server::{dispatch::Dispatcher, local_clients::LocalClients},
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

/// Starts the thread of the server `name`, and returns its command channel once the thread has
/// set up its iceoryx2 ports.
pub(super) fn spawn(name: &str) -> Result<(mpsc::Sender<Command>, JoinHandle<Result<()>>)> {
    let (started_tx, started_rx) = mpsc::channel();
    let (command_tx, command_rx) = mpsc::channel();
    let name = name.to_string();
    let thread = thread::Builder::new()
        .name(format!("gabriel-server-{name}"))
        .spawn(move || {
            // Created inside the thread: iceoryx2 ports cannot be moved between threads.
            let opened = Dispatcher::new(&name).and_then(|dispatcher| {
                let clients = LocalClients::open(dispatcher.node(), &name)?;
                Ok((dispatcher, clients))
            });
            let (dispatcher, clients) = match opened {
                Ok(opened) => {
                    let _ = started_tx.send(Ok(()));
                    opened
                }
                Err(e) => {
                    let _ = started_tx.send(Err(e));
                    return Ok(());
                }
            };
            run(dispatcher, clients, &command_rx)
        })
        .map_err(|e| Error::Internal(format!("failed to spawn server thread: {e}")))?;
    match started_rx.recv() {
        Ok(Ok(())) => Ok((command_tx, thread)),
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
    mut clients: LocalClients,
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

        dispatcher.wait(clients.input_ready(), COMMAND_POLL_INTERVAL)?;
        let now = Instant::now();
        // An error here concerns one call, one input or one engine. A failed call was answered
        // with the error, and an input's offer has already ended for the client, or times out
        // there. So the server carries on.
        let _ = clients.answer_calls(&mut dispatcher);
        let _ = clients.take_inputs(&mut dispatcher, now);
        let _ = dispatcher.poll(now);
        if now >= next_engine_check {
            let _ = dispatcher.check_engines(now);
            next_engine_check = now + ENGINE_CHECK_INTERVAL;
        }
        let _ = clients.send_events(&mut dispatcher);
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
