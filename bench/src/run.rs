//! Runs a flow in this process: a local server, a synthetic engine per engine of the flow, a
//! client, and a producer per producer, each on its own thread, plus a thread per producer that
//! records the events about its inputs. iceoryx2 carries everything between them as it would
//! between processes.

use std::{
    sync::{
        Arc, Barrier,
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use anyhow::{Context, Result, anyhow};
use gabriel::{Client, Engine, Error, Producer, Server, Subscription, Timestamp};
use rand::{RngExt, SeedableRng, rngs::StdRng};

use crate::{
    record::{EventRow, InputRow},
    spec::{EngineLoad, ProducerLoad, RunSpec},
};

/// How long a waiting engine or recorder sleeps before it checks whether the run is over.
const STOP_POLL_INTERVAL: Duration = Duration::from_millis(10);
/// The client has no remote server yet, so its address is never used.
const UNUSED_REMOTE_ADDR: &str = "127.0.0.1:0";

/// What a run recorded.
pub struct Recorded {
    pub inputs: Vec<InputRow>,
    pub events: Vec<EventRow>,
}

/// Runs `spec`, and returns what it recorded.
pub fn run(spec: &RunSpec) -> Result<Recorded> {
    let server_name = format!("{}-server", spec.name);
    let mut server = Server::new(&server_name, None).context("failed to start the server")?;
    let stop = Arc::new(AtomicBool::new(false));

    // Each thread gets its own stream of random numbers, derived from the run's seed.
    let mut seeds = StdRng::seed_from_u64(spec.seed);
    let mut engines = Vec::new();
    for (name, load) in &spec.engines {
        engines.push(start_engine(name, load, seeds.random(), &stop)?);
        server.add_engine(name)?;
    }

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
        UNUSED_REMOTE_ADDR,
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
    join_all(engines)?;
    // The client removes its flow from the server as it stops.
    drop(client);
    drop(server);
    Ok(Recorded { inputs, events })
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
/// time, then passes the input on or stops it, until the run is over.
fn start_engine(
    name: &str,
    load: &EngineLoad,
    seed: u64,
    stop: &Arc<AtomicBool>,
) -> Result<JoinHandle<Result<Vec<()>>>> {
    let (engine_name, stop) = (name.to_owned(), Arc::clone(stop));
    let service_time = load.service_time.sampler()?;
    let pass_probability = load.pass_probability;
    let result_size = load.result_size;
    let result = vec![0; result_size];
    spawn(
        format!("engine {name}"),
        move || Ok(Engine::new(&engine_name, None, result_size)?),
        move |mut engine| {
            let mut rng = StdRng::seed_from_u64(seed);
            while !stop.load(Ordering::Relaxed) {
                match engine.wait(STOP_POLL_INTERVAL) {
                    Ok(()) | Err(Error::Again) => {}
                    Err(e) => return Err(e.into()),
                }
                match engine.poll_input() {
                    Ok(_) => {}
                    Err(Error::Again) => continue,
                    Err(e) => return Err(e.into()),
                }
                thread::sleep(service_time.sample(&mut rng));
                if rng.random_bool(pass_probability) {
                    engine.pass(&result)?;
                } else {
                    engine.stop(&result)?;
                }
            }
            Ok(Vec::new())
        },
    )
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
    let input_size = load.input_size;
    spawn(
        format!("producer {name}"),
        move || Ok(Producer::new(&producer_name, input_size)?),
        move |mut producer| {
            let mut rng = StdRng::seed_from_u64(seed);
            let mut inputs = Vec::new();
            start.wait();
            let end = Instant::now() + duration;
            // Inputs are due at fixed times from the start, so a late publish does not delay the
            // ones after it.
            let mut due = Instant::now();
            while due < end {
                thread::sleep(due.saturating_duration_since(Instant::now()));
                for byte in producer.new_input()? {
                    byte.write(0);
                }
                let published_at = Timestamp::now();
                let id = producer.publish_input(input_size, &flow)?;
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
