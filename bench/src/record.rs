//! What a run records, and how it is written: one CSV row per published input and one per event,
//! in long form, which is what pandas and polars work with best. Times are nanoseconds on the
//! machine's monotonic clock.

use std::{fs, path::Path, time::Duration};

use anyhow::{Context, Result};
use gabriel::{InputId, Timestamp, subscription::Event};
use serde::Serialize;

/// An input a producer published.
#[derive(Debug, Serialize)]
pub struct InputRow {
    pub producer: String,
    pub session: u64,
    pub seq: u64,
    /// Taken just before the input was published.
    pub published_ns: u64,
}

impl InputRow {
    pub fn new(id: &InputId, published_at: Timestamp) -> Self {
        Self {
            producer: id.producer().to_owned(),
            session: id.session(),
            seq: id.seq(),
            published_ns: published_at.as_nanos(),
        }
    }
}

/// An input a synthetic engine worked on: when it started, once it had its accelerator, and for
/// how long. The event's service time also counts the wait for the accelerator; this does not.
#[derive(Debug, Serialize)]
pub struct WorkRow {
    pub engine: String,
    pub producer: String,
    pub session: u64,
    pub seq: u64,
    pub started_ns: u64,
    pub work_ns: u64,
}

impl WorkRow {
    pub fn new(engine: &str, id: &InputId, started_at: Timestamp, work: Duration) -> Self {
        Self {
            engine: engine.to_owned(),
            producer: id.producer().to_owned(),
            session: id.session(),
            seq: id.seq(),
            started_ns: started_at.as_nanos(),
            work_ns: u64::try_from(work.as_nanos()).unwrap_or(u64::MAX),
        }
    }
}

/// An event a producer's subscription read. Empty fields do not apply to the event's kind.
#[derive(Debug, Serialize)]
pub struct EventRow {
    /// `Sent`, `Result`, `Skipped`, `TimedOut`, `Disconnected`, or `Lost` when events were lost.
    pub kind: String,
    pub producer: String,
    pub session: Option<u64>,
    pub seq: Option<u64>,
    pub flow: String,
    pub engine: String,
    /// What the engine forwarded, for `Result`.
    pub forward: Option<String>,
    /// The size of the engine's result in bytes, for `Result`; e.g. a detector's grows with the
    /// objects it found.
    pub result_len: Option<usize>,
    /// When the server made the engine's offer.
    pub offered_ns: Option<u64>,
    /// When the event happened.
    pub at_ns: Option<u64>,
    /// When the client received the event from the remote server.
    pub received_ns: Option<u64>,
    /// How many events were lost, for `Lost`.
    pub lost: Option<u64>,
}

impl EventRow {
    /// The row of an event about the inputs of `producer`.
    pub fn new(producer: &str, event: &Event<'_>) -> Self {
        match event {
            Event::Lost(lost) => Self {
                kind: String::from("Lost"),
                producer: producer.to_owned(),
                session: None,
                seq: None,
                flow: String::new(),
                engine: String::new(),
                forward: None,
                result_len: None,
                offered_ns: None,
                at_ns: None,
                received_ns: None,
                lost: Some(lost.count()),
            },
            Event::Published(event) => {
                let kind = event.kind();
                let names_input = kind != gabriel::EventKind::Disconnected;
                Self {
                    kind: format!("{kind:?}"),
                    producer: producer.to_owned(),
                    session: names_input.then(|| event.id().session()),
                    seq: names_input.then(|| event.id().seq()),
                    flow: event.flow().to_owned(),
                    engine: event.engine().to_owned(),
                    forward: (kind == gabriel::EventKind::Result)
                        .then(|| format!("{:?}", event.forward())),
                    result_len: (kind == gabriel::EventKind::Result).then(|| event.result().len()),
                    offered_ns: event.offered_at().map(|at| at.as_nanos()),
                    at_ns: Some(event.at().as_nanos()),
                    received_ns: event.received_at().map(|at| at.as_nanos()),
                    lost: None,
                }
            }
        }
    }
}

/// The names of the files a run writes in its output directory.
pub const INPUTS_FILE: &str = "inputs.csv";
pub const EVENTS_FILE: &str = "events.csv";
pub const WORK_FILE: &str = "work.csv";
/// A copy of the run's TOML file, so the rows are never separated from what produced them.
pub const RUN_FILE: &str = "run.toml";
/// When and from which commit the run was made.
pub const META_FILE: &str = "meta.toml";

/// Writes `rows` as CSV to the file `name` in `dir`.
pub fn write_csv<T: Serialize>(dir: &Path, name: &str, rows: &[T]) -> Result<()> {
    let path = dir.join(name);
    let mut writer =
        csv::Writer::from_path(&path).with_context(|| format!("failed to create {name}"))?;
    for row in rows {
        writer.serialize(row)?;
    }
    writer.flush()?;
    Ok(())
}

/// Writes `text` to the file `name` in `dir`.
pub fn write_text(dir: &Path, name: &str, text: &str) -> Result<()> {
    fs::write(dir.join(name), text).with_context(|| format!("failed to write {name}"))
}
