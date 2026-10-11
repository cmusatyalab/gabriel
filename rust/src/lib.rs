//! Gabriel: token-based flow control between producers and engines, built on
//! iceoryx2 for zero-copy local transport.
//!
//! The idiomatic Rust API lives in the [`producer`], [`client`], [`server`]
//! and [`engine`] modules, flows are described with [`spec`], and a producer's app reads what
//! happened to its inputs with a [`subscription`]. Every event carries the time it happened, on
//! the shared [`clock`]. The C ABI in [`ffi`] is a thin wrapper over it.

pub mod client;
pub mod clock;
pub mod engine;
pub mod error;
pub mod ffi;
pub mod logging;
pub mod naming;
pub mod producer;
pub mod server;
pub mod spec;
pub mod subscription;

pub use client::{Client, RemoteServerConfig};
pub use clock::Timestamp;
pub use engine::{Engine, Input};
pub use error::{Error, Result};
pub use ipc::types::{EventKind, Forward};
pub use producer::Producer;
pub use routing::ids::InputId;
pub use server::Server;
pub use spec::FlowSpec;
pub use subscription::Subscription;

mod envelope;
mod ipc;
mod net;
mod routing;
mod services;
#[cfg(test)]
mod test_util;
mod wire;
