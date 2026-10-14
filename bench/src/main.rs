//! Runs a Gabriel flow with synthetic engines, and records when each input reached each stage.
//!
//! A run is described by a TOML file (see `runs/`): the flow, how fast each producer publishes,
//! and how long each engine works on an input. The run writes, into its output directory:
//! `inputs.csv` (one row per published input), `events.csv` (one row per event about an input),
//! a copy of the run's file, and `meta.toml` (when, and from which commit). `analyze.py` turns
//! them into staleness, drops, and throughput.
//!
//! ```text
//! cargo run --release -p gabriel-bench -- bench/runs/chain.toml
//! ```
//!
//! A run with a `[network]` section emulates the link to the remote server on loopback, so it must
//! run in a network namespace of its own (see [`network`]).

mod network;
mod record;
mod run;
mod spec;

use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result};
use clap::Parser;
use serde::Serialize;

use crate::{
    record::{EVENTS_FILE, INPUTS_FILE, META_FILE, RUN_FILE, WORK_FILE},
    spec::RunSpec,
};

/// The directory output directories go in, unless `--out` is given.
const RESULTS_DIR: &str = "bench-results";

#[derive(Debug, Parser)]
#[command(about = "Runs a Gabriel flow with synthetic or external engines and records its events")]
struct Args {
    /// The run's TOML file.
    run: PathBuf,
    /// Where to write the results. Defaults to `bench-results/<name>-<unix time>`.
    #[arg(long)]
    out: Option<PathBuf>,
    /// Which of Gabriel's log messages to print, in the syntax of RUST_LOG, e.g. "info".
    #[arg(long)]
    log: Option<String>,
}

/// When and from what a run was made.
#[derive(Debug, Serialize)]
struct Meta {
    /// Seconds since the Unix epoch.
    started_unix: u64,
    /// The commit of the working tree, if it is a git repository.
    git_commit: Option<String>,
    /// Whether the working tree had changes that were not committed.
    git_dirty: Option<bool>,
    inputs: usize,
    events: usize,
}

fn main() -> Result<()> {
    let args = Args::parse();
    gabriel::logging::init(args.log.as_deref())?;
    let (spec, text) = RunSpec::read(&args.run)?;
    if let Some(network) = &spec.network {
        network::emulate(network).context("failed to emulate the network")?;
    }
    let started_unix = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let out = args
        .out
        .unwrap_or_else(|| Path::new(RESULTS_DIR).join(format!("{}-{started_unix}", spec.name)));
    fs::create_dir_all(&out).with_context(|| format!("failed to create {}", out.display()))?;

    eprintln!(
        "running {:?} for {}, then {} to drain",
        spec.name,
        humantime::format_duration(spec.duration),
        humantime::format_duration(spec.drain)
    );
    let recorded = run::run(&spec)?;

    record::write_csv(&out, INPUTS_FILE, &recorded.inputs)?;
    record::write_csv(&out, EVENTS_FILE, &recorded.events)?;
    record::write_csv(&out, WORK_FILE, &recorded.work)?;
    record::write_text(&out, RUN_FILE, &text)?;
    let meta = Meta {
        started_unix,
        git_commit: git(&["rev-parse", "HEAD"]),
        git_dirty: git(&["status", "--porcelain"]).map(|status| !status.is_empty()),
        inputs: recorded.inputs.len(),
        events: recorded.events.len(),
    };
    record::write_text(&out, META_FILE, &toml::to_string(&meta)?)?;
    eprintln!(
        "recorded {} inputs and {} events in {}",
        meta.inputs,
        meta.events,
        out.display()
    );
    Ok(())
}

/// The trimmed output of `git args`, or `None` if git fails, e.g. outside a repository.
fn git(args: &[&str]) -> Option<String> {
    let output = Command::new("git").args(args).output().ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned())
}
