//! Engine: consumes inputs and publishes results back to their producers.

use std::ffi::CStr;

use crate::Result;

/// An input held by the engine until [`Engine::release_input`] is called.
#[derive(Debug)]
pub struct Input<'a> {
    /// The input payload.
    pub data: &'a [u8],
    /// Name of the producer the input came from.
    pub producer: &'a CStr,
}

/// Processes inputs and replies with results of at most `max_result_size` bytes.
#[derive(Debug)]
pub struct Engine {}

impl Engine {
    /// Creates the engine `name` (service [`crate::naming::engine`]).
    pub fn new(name: &str, max_result_size: usize) -> Result<Self> {
        todo!()
    }

    /// Returns the next input, or [`crate::Error::Again`] if none is available.
    /// The input stays valid until [`Engine::release_input`].
    pub fn poll_input(&mut self) -> Result<Input<'_>> {
        todo!()
    }

    /// Releases the input returned by the last [`Engine::poll_input`].
    pub fn release_input(&mut self) -> Result<()> {
        todo!()
    }

    /// Publishes `result` back to `producer`.
    pub fn publish_result(&mut self, producer: &str, result: &[u8]) -> Result<()> {
        let _ = (producer, result);
        todo!()
    }
}
