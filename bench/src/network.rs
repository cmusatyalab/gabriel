//! Emulates the link to the remote server. The remote server listens on loopback, and netem on the
//! loopback interface delays, varies, drops, and limits its packets. iceoryx2 uses shared memory,
//! not the network, so only the client's QUIC connection is affected.
//!
//! A packet passes netem once each way, so both directions get the same settings.
//!
//! Changing loopback would slow every program on the machine, so the run must have its own network
//! namespace, which needs no root:
//!
//! ```text
//! unshare --user --map-root-user --net target/release/gabriel-bench bench/runs/remote.toml
//! ```

use std::{fs, process::Command, time::Duration};

use anyhow::{Context, Result, bail, ensure};

use crate::spec::NetworkSpec;

/// The loopback interface, which the remote server listens on.
const LOOPBACK: &str = "lo";
/// The interfaces of the network namespace, one per line after two lines of headings.
const INTERFACES: &str = "/proc/net/dev";
const INTERFACES_HEADER_LINES: usize = 2;
/// How the command to run in a namespace of its own reads.
const UNSHARE: &str = "unshare --user --map-root-user --net";
const PERCENT: f64 = 100.0;
/// The rate given to netem when the run sets none: far above what a run sends, so it never limits.
/// netem keeps packets in order only when it has a rate. Without one, jitter reorders them, which
/// QUIC takes for loss, so it slows down far more than the jitter alone would make it.
const UNLIMITED_RATE: &str = "100gbit";

/// Brings loopback up, and gives it the delay, jitter, loss and rate of `network`.
pub fn emulate(network: &NetworkSpec) -> Result<()> {
    ensure_own_namespace()?;
    run("ip", &["link", "set", LOOPBACK, "up"])?;
    let mut netem = vec![String::from("delay"), microseconds(network.delay)];
    if !network.jitter.is_zero() {
        netem.push(microseconds(network.jitter));
    }
    if network.loss > 0.0 {
        netem.extend([String::from("loss"), format!("{}%", network.loss * PERCENT)]);
    }
    let rate = network.rate.as_deref().unwrap_or(UNLIMITED_RATE);
    netem.extend([String::from("rate"), String::from(rate)]);
    let mut args = vec!["qdisc", "replace", "dev", LOOPBACK, "root", "netem"];
    args.extend(netem.iter().map(String::as_str));
    run("tc", &args)
}

/// Fails unless loopback is the only interface, as in a network namespace made for this run.
fn ensure_own_namespace() -> Result<()> {
    let interfaces = fs::read_to_string(INTERFACES).context("failed to list the interfaces")?;
    let others: Vec<&str> = interfaces
        .lines()
        .skip(INTERFACES_HEADER_LINES)
        .filter_map(|line| line.split(':').next())
        .map(str::trim)
        .filter(|name| *name != LOOPBACK)
        .collect();
    ensure!(
        others.is_empty(),
        "the run emulates a network on loopback, which would slow every program here (this \
         namespace also has {others:?}); run it in a network namespace of its own: {UNSHARE} \
         target/release/gabriel-bench <run>"
    );
    Ok(())
}

/// tc's syntax for `duration`.
fn microseconds(duration: Duration) -> String {
    format!("{}us", duration.as_micros())
}

fn run(program: &str, args: &[&str]) -> Result<()> {
    let output = Command::new(program)
        .args(args)
        .output()
        .with_context(|| format!("failed to run {program}"))?;
    if !output.status.success() {
        bail!(
            "{program} {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&output.stderr).trim()
        );
    }
    Ok(())
}
