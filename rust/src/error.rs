//! Error type shared by the Rust API.

/// Errors returned by Gabriel operations. A server sends them back to its clients, so they can be
/// serialized.
#[derive(Debug, thiserror::Error, serde::Serialize, serde::Deserialize)]
pub enum Error {
    /// A caller-supplied argument was invalid (null pointer, bad UTF-8, bad address, ...).
    #[error("invalid argument: {0}")]
    InvalidArgument(String),

    /// The payload is larger than the configured maximum size.
    #[error("size {size} exceeds maximum of {max}")]
    SizeExceeded { size: usize, max: usize },

    /// Nothing is available right now; try again later.
    #[error("nothing available, try again")]
    Again,

    /// An internal failure, typically from the iceoryx2 transport.
    #[error("internal error: {0}")]
    Internal(String),
}

pub type Result<T> = std::result::Result<T, Error>;
