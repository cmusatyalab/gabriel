//! Gabriel: token-based flow control between producers and engines, built on
//! iceoryx2 for zero-copy local transport.
//!
//! The idiomatic Rust API lives in the [`producer`], [`client`], [`server`]
//! and [`engine`] modules. The C ABI in [`ffi`] is a thin wrapper over it.

pub mod client;
pub mod engine;
pub mod error;
pub mod ffi;
pub mod naming;
pub mod producer;
pub mod server;

pub use client::Client;
pub use engine::{Engine, Input};
pub use error::{Error, Result};
pub use producer::Producer;
pub use server::Server;

mod envelope;
mod routing;
mod services;
#[cfg(test)]
mod test_util;
