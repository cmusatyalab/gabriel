//! Runs a Gabriel server that remote clients reach over the network, and serves the engines on
//! its machine to them, e.g. on an edge server, next to engines in their own containers.
//!
//! The server keeps its certificate in its identity directory, so that it stays the same across
//! restarts. Give clients a copy of it. The server stops on Ctrl-C or SIGTERM.
//!
//! The server also serves Prometheus metrics: its engines' (see `metrics`), and Gabriel's own
//! (see `gabriel::metrics`).

mod metrics;

use std::{
    net::SocketAddr,
    path::PathBuf,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

use anyhow::{Context, Result};
use clap::Parser;
use gabriel::{Server, logging, server::CERTIFICATE_FILE};
use signal_hook::{
    consts::{SIGINT, SIGTERM},
    iterator::Signals,
};

/// Where the server accepts remote clients unless told otherwise: every address, on the port
/// Gabriel's examples use.
const DEFAULT_LISTEN_ADDR: &str = "0.0.0.0:4433";
/// Where the server serves its metrics unless told otherwise: the port the Python Gabriel server
/// served them on.
const DEFAULT_METRICS_ADDR: &str = "0.0.0.0:8000";

#[derive(Parser)]
#[command(version, about)]
struct Args {
    /// The server's name. Engines and local clients on this machine find the server by it.
    #[arg(long, default_value = "gabriel")]
    name: String,

    /// The address and port to accept remote clients on.
    #[arg(long, default_value = DEFAULT_LISTEN_ADDR)]
    listen: String,

    /// The directory to keep the server's certificate and key in. It is created if missing.
    #[arg(long)]
    identity_dir: PathBuf,

    /// An engine to serve. Give it once per engine. An engine may start before or after the
    /// server.
    #[arg(long = "engine", required = true)]
    engines: Vec<String>,

    /// The address and port to serve Prometheus metrics on, at /metrics.
    #[arg(long, default_value = DEFAULT_METRICS_ADDR)]
    metrics_listen: SocketAddr,
}

fn main() -> Result<()> {
    let args = Args::parse();
    logging::init(None)?;
    std::fs::create_dir_all(&args.identity_dir).with_context(|| {
        format!(
            "failed to create the identity directory {}",
            args.identity_dir.display()
        )
    })?;
    // Registered first, so that a signal while the server starts stops it cleanly rather than
    // killing it.
    let mut signals = Signals::new([SIGINT, SIGTERM])?;

    // Before the server, so that the metrics it registers are recorded.
    gabriel::metrics::serve(args.metrics_listen)
        .with_context(|| format!("failed to serve metrics on {}", args.metrics_listen))?;
    let mut server = Server::new(&args.name, Some(&args.listen), Some(&args.identity_dir))
        .context("failed to start the server")?;
    for engine in &args.engines {
        server
            .add_engine(engine)
            .with_context(|| format!("failed to serve engine {engine:?}"))?;
    }
    let addr = server
        .remote_addr()
        .expect("a server given an address accepts remote clients");
    eprintln!(
        "server {} serves {} on {addr}; clients trust {}",
        args.name,
        args.engines.join(", "),
        args.identity_dir.join(CERTIFICATE_FILE).display(),
    );

    let stop_watching = Arc::new(AtomicBool::new(false));
    let watchers = metrics::watch_engines(&args.engines, &stop_watching);
    eprintln!("metrics on http://{}/metrics", args.metrics_listen);

    signals.forever().next();
    eprintln!("stopping");
    stop_watching.store(true, Ordering::Relaxed);
    for watcher in watchers {
        // A watcher that panicked has nothing left to stop.
        let _ = watcher.join();
    }
    Ok(())
}
