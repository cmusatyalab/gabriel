//! Gabriel: token-based flow control between producers and engines, built on
//! iceoryx2 for local transport through shared memory.
//!
//! The idiomatic Rust API lives in the [`producer`], [`client`], [`server`]
//! and [`engine`] modules, flows are described with [`spec`], and a producer's app reads what
//! happened to its inputs with a [`subscription`]. Anyone can read a producer's inputs themselves
//! with a [`reader`]. Every event carries the time it happened, on the shared [`clock`]. The C ABI in [`ffi`] is a thin wrapper over it.

pub mod client;
pub mod clock;
pub mod engine;
pub mod error;
pub mod ffi;
pub mod logging;
pub mod metrics;
pub mod naming;
pub mod producer;
pub mod reader;
pub mod server;
pub mod spec;
pub mod subscription;

pub use client::{Client, RemoteServerConfig};
pub use clock::Timestamp;
pub use engine::{Engine, Input};
pub use error::{Error, Result};
pub use ipc::types::{EventKind, Forward};
pub use producer::Producer;
pub use reader::Reader;
pub use routing::ids::InputId;
pub use server::Server;
pub use spec::FlowSpec;
pub use subscription::Subscription;

mod envelope;
mod events;
mod ipc;
mod net;
mod routing;
mod services;
#[cfg(test)]
mod test_util;
mod wire;
