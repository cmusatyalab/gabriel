//! Runs a flow in this process: a local server, a remote server if the flow has remote engines, a
//! synthetic engine per engine of the flow, a client, and a producer per producer, each on its own
//! thread, plus a thread per producer that records the events about its inputs. iceoryx2 carries
//! everything between them as it would between processes, and QUIC on loopback carries what goes
//! between the client and the remote server. External engines run in their own processes, and the
//! servers only take them in. A remote server already running elsewhere, e.g. on an edge server,
//! takes the place of the bench's own, and serves the remote engines itself.

use std::{
    collections::{BTreeSet, HashMap},
    net::ToSocketAddrs,
    sync::{
        Arc, Barrier, Mutex,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use anyhow::{Context, Result, anyhow};
use gabriel::{
    Client, Engine, Error, Producer, RemoteServerConfig, Server, Subscription, Timestamp,
    spec::Location,
};
use rand::{RngExt, SeedableRng, rngs::StdRng};

use crate::{
    record::{EventRow, InputRow, WorkRow},
    spec::{EngineLoad, ProducerLoad, RunSpec},
};

/// How long a waiting engine or recorder sleeps before it checks whether the run is over.
const STOP_POLL_INTERVAL: Duration = Duration::from_millis(10);
/// The remote server listens on loopback, on a port the system picks. A network emulated on
/// loopback applies to it.
const REMOTE_BIND_ADDR: &str = "127.0.0.1:0";

/// What a run recorded.
pub struct Recorded {
    pub inputs: Vec<InputRow>,
    pub events: Vec<EventRow>,
    pub work: Vec<WorkRow>,
}

/// Runs `spec`, and returns what it recorded.
pub fn run(spec: &RunSpec) -> Result<Recorded> {
    let server_name = format!("{}-server", spec.name);
    let mut server = Server::new(&server_name, None, None).context("failed to start the server")?;
    let remote_engines: BTreeSet<&str> = spec
        .flow
        .engines
        .iter()
        .filter(|engine| engine.location == Location::Remote)
        .map(|engine| engine.name.as_str())
        .collect();
    // A remote server elsewhere is already running; otherwise the bench runs one.
    let mut remote = (!remote_engines.is_empty() && spec.remote.addr.is_none())
        .then(|| {
            Server::new(
                &format!("{}-remote", spec.name),
                Some(REMOTE_BIND_ADDR),
                None,
            )
        })
        .transpose()
        .context("failed to start the remote server")?;
    let stop = Arc::new(AtomicBool::new(false));

    // Each thread gets its own stream of random numbers, derived from the run's seed.
    let mut seeds = StdRng::seed_from_u64(spec.seed);
    let mut accelerators: HashMap<&str, Arc<Mutex<()>>> = HashMap::new();
    let mut engines = Vec::new();
    for (name, load) in &spec.engines {
        let accelerator = load
            .accelerator
            .as_deref()
            .map(|accelerator| Arc::clone(accelerators.entry(accelerator).or_default()));
        // Every engine draws a seed, so adding an external engine leaves the others' draws as
        // they were.
        let seed = seeds.random();
        if !load.external {
            engines.push(start_engine(name, load, accelerator, seed, &stop)?);
        }
        if !remote_engines.contains(name.as_str()) {
            server.add_engine(name)?;
        } else if let Some(remote) = &mut remote {
            remote.add_engine(name)?;
        }
        // Otherwise the remote server elsewhere serves the engine.
    }
    let remote_config = remote_config(spec, remote.as_ref())?;

    // Every producer and recorder is ready before the client takes their inputs, and they start
    // together once the flow is added.
    let start = Arc::new(Barrier::new(spec.producers.len() + 1));
    let mut producers = Vec::new();
    let mut recorders = Vec::new();
    for (name, load) in &spec.producers {
        recorders.push(start_recorder(name, &stop)?);
        producers.push(start_producer(
            name,
            &spec.name,
            load,
            spec.duration,
            seeds.random(),
            &start,
        )?);
    }
    let mut client = Client::new(
        &format!("{}-client", spec.name),
        &server_name,
        remote_config,
    )
    .context("failed to start the client")?;
    for name in spec.producers.keys() {
        client.add_producer(name)?;
    }
    client
        .add_flow(&spec.name, &spec.flow)
        .context("failed to add the flow")?;

    start.wait();
    let inputs = join_all(producers)?;
    thread::sleep(spec.drain);
    stop.store(true, Ordering::Relaxed);
    let events = join_all(recorders)?;
    let work = join_all(engines)?;
    // The client removes its flow from the servers as it stops.
    drop(client);
    drop(remote);
    drop(server);
    Ok(Recorded {
        inputs,
        events,
        work,
    })
}

/// Waits for every thread, and gathers the rows they recorded.
fn join_all<T>(threads: Vec<JoinHandle<Result<Vec<T>>>>) -> Result<Vec<T>> {
    let mut rows = Vec::new();
    for thread in threads {
        let recorded = thread
            .join()
            .map_err(|_| anyhow!("a benchmark thread panicked"))??;
        rows.extend(recorded);
    }
    Ok(rows)
}

/// Spawns a thread named `name` that runs `body` on what `setup` creates in it, and returns once
/// `setup` succeeded. iceoryx2 ports cannot move between threads, so they are created in the
/// thread that uses them.
fn spawn<S, T: Send + 'static>(
    name: String,
    setup: impl FnOnce() -> Result<S> + Send + 'static,
    body: impl FnOnce(S) -> Result<Vec<T>> + Send + 'static,
) -> Result<JoinHandle<Result<Vec<T>>>> {
    let (ready_tx, ready_rx) = mpsc::channel();
    let thread = thread::Builder::new()
        .name(name.clone())
        .spawn(move || match setup() {
            Ok(state) => {
                let _ = ready_tx.send(Ok(()));
                body(state)
            }
            Err(e) => {
                let _ = ready_tx.send(Err(e));
                Ok(Vec::new())
            }
        })?;
    ready_rx
        .recv()
        .map_err(|_| anyhow!("thread {name} stopped during setup"))?
        .with_context(|| format!("failed to set up {name}"))?;
    Ok(thread)
}

/// Starts the synthetic engine `name`: it works on each input for a time drawn from its service
/// time, then passes the input on or stops it, until the run is over. With an `accelerator`, it
/// holds it while it works. Returns what it worked on.
fn start_engine(
    name: &str,
    load: &EngineLoad,
    accelerator: Option<Arc<Mutex<()>>>,
    seed: u64,
    stop: &Arc<AtomicBool>,
) -> Result<JoinHandle<Result<Vec<WorkRow>>>> {
    let (engine_name, name, stop) = (name.to_owned(), name.to_owned(), Arc::clone(stop));
    let service_time = load.service_time()?;
    let pass_probability = load.pass_probability;
    let result_size = load.result_size;
    let result = vec![0; result_size];
    spawn(
        format!("engine {name}"),
        move || Ok(Engine::new(&engine_name, None, result_size)?),
        move |mut engine| {
            let mut rng = StdRng::seed_from_u64(seed);
            let mut worked = Vec::new();
            while !stop.load(Ordering::Relaxed) {
                match engine.wait(STOP_POLL_INTERVAL) {
                    Ok(()) | Err(Error::Again) => {}
                    Err(e) => return Err(e.into()),
                }
                let id = match engine.poll_input() {
                    Ok(input) => *input.id(),
                    Err(Error::Again) => continue,
                    Err(e) => return Err(e.into()),
                };
                let work = service_time.sample(&mut rng);
                // A poisoned lock only means another engine's thread panicked; the run fails
                // when that thread is joined.
                let turn = accelerator
                    .as_ref()
                    .map(|accelerator| accelerator.lock().unwrap_or_else(|e| e.into_inner()));
                worked.push(WorkRow::new(&name, &id, Timestamp::now(), work));
                thread::sleep(work);
                drop(turn);
                if rng.random_bool(pass_probability) {
                    engine.forward(&result)?;
                } else {
                    engine.drop(&result)?;
                }
            }
            Ok(worked)
        },
    )
}

/// How the client reaches the remote server: the one the bench runs, `remote`, or the one already
/// running at the spec's address. None if the flow has no remote engines.
fn remote_config(spec: &RunSpec, remote: Option<&Server>) -> Result<Option<RemoteServerConfig>> {
    let crossing_capacity = spec.remote.crossing_capacity;
    if let Some(remote) = remote {
        return Ok(Some(RemoteServerConfig {
            addr: remote
                .remote_addr()
                .expect("the remote server accepts remote clients"),
            certificate: remote
                .certificate()
                .expect("the remote server accepts remote clients")
                .to_vec(),
            crossing_capacity,
        }));
    }
    let (Some(addr), Some(certificate)) = (&spec.remote.addr, &spec.remote.certificate) else {
        return Ok(None);
    };
    Ok(Some(RemoteServerConfig {
        addr: addr
            .to_socket_addrs()
            .with_context(|| format!("failed to resolve the remote server {addr}"))?
            .next()
            .with_context(|| format!("the remote server {addr} has no address"))?,
        certificate: std::fs::read(certificate).with_context(|| {
            format!(
                "failed to read the remote server's certificate {}",
                certificate.display()
            )
        })?,
        crossing_capacity,
    }))
}

/// Starts the producer `name`, which publishes on `flow` from the start of the run for
/// `duration`, and returns when it published each input.
fn start_producer(
    name: &str,
    flow: &str,
    load: &ProducerLoad,
    duration: Duration,
    seed: u64,
    start: &Arc<Barrier>,
) -> Result<JoinHandle<Result<Vec<InputRow>>>> {
    let (producer_name, flow, start) = (name.to_owned(), flow.to_owned(), Arc::clone(start));
    let gaps = load.gaps()?;
    let payloads = load.payloads()?;
    let max_input_size = payloads.iter().map(Vec::len).max().unwrap_or_default();
    let input_type = load.input_type.clone();
    spawn(
        format!("producer {name}"),
        move || {
            Ok(Producer::new(
                &producer_name,
                input_type.as_deref(),
                max_input_size,
            )?)
        },
        move |mut producer| {
            let mut rng = StdRng::seed_from_u64(seed);
            let mut inputs = Vec::new();
            start.wait();
            let end = Instant::now() + duration;
            // Inputs are due at fixed times from the start, so a late publish does not delay the
            // ones after it.
            let mut due = Instant::now();
            for payload in payloads.iter().cycle() {
                if due >= end {
                    break;
                }
                thread::sleep(due.saturating_duration_since(Instant::now()));
                producer.new_input()?[..payload.len()].write_copy_of_slice(payload);
                let published_at = Timestamp::now();
                let id = producer.publish_input(payload.len(), &flow)?;
                inputs.push(InputRow::new(&id, published_at));
                due += gaps.sample(&mut rng);
            }
            Ok(inputs)
        },
    )
}

/// Starts recording the events about the inputs of the producer `name`, until the run is over.
fn start_recorder(name: &str, stop: &Arc<AtomicBool>) -> Result<JoinHandle<Result<Vec<EventRow>>>> {
    let (producer, stop) = (name.to_owned(), Arc::clone(stop));
    spawn(
        format!("recorder {name}"),
        {
            let producer = producer.clone();
            move || Ok(Subscription::producer(&producer)?)
        },
        move |mut subscription| {
            let mut events = Vec::new();
            while !stop.load(Ordering::Relaxed) {
                match subscription.wait(STOP_POLL_INTERVAL) {
                    Ok(()) | Err(Error::Again) => {}
                    Err(e) => return Err(e.into()),
                }
                loop {
                    match subscription.poll() {
                        Ok(event) => events.push(EventRow::new(&producer, &event)),
                        Err(Error::Again) => break,
                        Err(e) => return Err(e.into()),
                    }
                }
            }
            Ok(events)
        },
    )
}
