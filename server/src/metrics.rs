//! Prometheus metrics of the engines the server serves, for watching them live. They come from
//! each engine's events, which the server publishes on this machine, so they need no help from
//! the engines, and all their times are on this machine's clock:
//!
//! - `gabriel_engine_inputs_received_total`: inputs sent to the engine.
//! - `gabriel_engine_outcomes_total`, by `outcome`: how the engine's offers ended, `result`,
//!   `skipped` (replaced by a newer input before it was sent) or `timed_out`.
//! - `gabriel_engine_processing_latency_seconds`: from sending an input to the engine to its
//!   result.
//! - `gabriel_engine_wait_seconds`: from the offer to sending the input, i.e. how long the input
//!   waited for the engine to be free.
//! - `gabriel_events_lost_total`: events this server's watcher missed, so the counts above are
//!   short by up to that many.
//!
//! Each has an `engine_id` label, as in the Python Gabriel server.

use std::{
    collections::HashMap,
    net::SocketAddr,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::Duration,
};

use anyhow::{Context, Result};
use gabriel::{
    EventKind, InputId, Subscription, Timestamp,
    subscription::{Event, Published},
};
use metrics::{counter, histogram};
use metrics_exporter_prometheus::PrometheusBuilder;

/// Histogram buckets, in seconds: from a fast engine on a GPU to one that is badly overloaded.
const LATENCY_BUCKETS: &[f64] = &[
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
];
/// How long a watcher waits for an event before it checks whether to stop.
const STOP_CHECK_INTERVAL: Duration = Duration::from_millis(500);
/// How long a watcher remembers when an input was sent, waiting for its result. A late result
/// can still come after the offer timed out, so this is longer than any engine's timeout.
const SENT_WINDOW: Duration = Duration::from_secs(60);

/// Serves the metrics at `http://<addr>/metrics`, on a thread of the exporter's own.
pub fn serve(addr: SocketAddr) -> Result<()> {
    PrometheusBuilder::new()
        .with_http_listener(addr)
        .set_buckets(LATENCY_BUCKETS)
        .context("invalid histogram buckets")?
        .install()
        .with_context(|| format!("failed to serve metrics on {addr}"))
}

/// Watches the events of each of `engines` on a thread of its own, until `stop` is set.
pub fn watch_engines(engines: &[String], stop: &Arc<AtomicBool>) -> Vec<JoinHandle<()>> {
    engines
        .iter()
        .map(|engine| {
            let engine = engine.clone();
            let stop = Arc::clone(stop);
            thread::spawn(move || {
                if let Err(e) = watch_engine(&engine, &stop) {
                    eprintln!("stopped recording metrics of engine {engine:?}: {e:#}");
                }
            })
        })
        .collect()
}

/// Records the metrics of the engine `engine` from its events, until `stop` is set. A
/// subscription stays on the thread that created it, so it is created here.
fn watch_engine(engine: &str, stop: &AtomicBool) -> Result<()> {
    let mut events = Subscription::engine(engine)
        .with_context(|| format!("failed to subscribe to engine {engine:?}"))?;
    let mut sent_at = SentTimes::default();
    while !stop.load(Ordering::Relaxed) {
        match events.wait(STOP_CHECK_INTERVAL) {
            Ok(()) => {}
            Err(gabriel::Error::Again) => continue,
            Err(e) => return Err(e).context("failed to wait for an event"),
        }
        loop {
            match events.poll() {
                Ok(Event::Published(event)) => record(engine, &event, &mut sent_at),
                Ok(Event::Lost(lost)) => {
                    counter!("gabriel_events_lost_total", "engine_id" => engine.to_owned())
                        .increment(lost.count());
                }
                Err(gabriel::Error::Again) => break,
                Err(e) => return Err(e).context("failed to read an event"),
            }
        }
    }
    Ok(())
}

/// Records one event of the engine `engine`.
fn record(engine: &str, event: &Published, sent_at: &mut SentTimes) {
    let engine = engine.to_owned();
    let outcome = match event.kind() {
        EventKind::Sent => {
            counter!("gabriel_engine_inputs_received_total", "engine_id" => engine.clone())
                .increment(1);
            if let Some(offered_at) = event.offered_at() {
                histogram!("gabriel_engine_wait_seconds", "engine_id" => engine)
                    .record(event.at().saturating_duration_since(offered_at));
            }
            sent_at.insert(offer_key(event), event.at());
            return;
        }
        EventKind::Result => {
            if let Some(sent) = sent_at.remove(&offer_key(event)) {
                histogram!("gabriel_engine_processing_latency_seconds", "engine_id" => engine.clone())
                    .record(event.at().saturating_duration_since(sent));
            }
            "result"
        }
        EventKind::Skipped => "skipped",
        EventKind::TimedOut => "timed_out",
        // A derived result answers no offer, and the rest are not about an engine's offers.
        _ => return,
    };
    counter!("gabriel_engine_outcomes_total", "engine_id" => engine, "outcome" => outcome)
        .increment(1);
}

/// Identifies the engine's offer an event is about: one input can enter several flows.
type OfferKey = (InputId, String);

fn offer_key(event: &Published) -> OfferKey {
    (*event.id(), event.flow().to_owned())
}

/// When each input was sent to the engine, for inputs whose result has not come yet. Inputs that
/// never get one are forgotten after `SENT_WINDOW`.
#[derive(Default)]
struct SentTimes {
    times: HashMap<OfferKey, Timestamp>,
    pruned_at: Option<Timestamp>,
}

impl SentTimes {
    fn insert(&mut self, key: OfferKey, at: Timestamp) {
        self.times.insert(key, at);
        let due = self
            .pruned_at
            .is_none_or(|pruned| at.saturating_duration_since(pruned) >= SENT_WINDOW);
        if due {
            self.times
                .retain(|_, sent| at.saturating_duration_since(*sent) < SENT_WINDOW);
            self.pruned_at = Some(at);
        }
    }

    fn remove(&mut self, key: &OfferKey) -> Option<Timestamp> {
        self.times.remove(key)
    }
}
