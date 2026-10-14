//! Runs a Gabriel server that remote clients reach over the network, and serves the engines on
//! its machine to them, e.g. on an edge server, next to engines in their own containers.
//!
//! The server keeps its certificate in its identity directory, so that it stays the same across
//! restarts. Give clients a copy of it. The server stops on Ctrl-C or SIGTERM.

use std::path::PathBuf;

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

    signals.forever().next();
    eprintln!("stopping");
    Ok(())
}
