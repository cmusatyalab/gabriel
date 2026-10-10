//! Logging. Gabriel logs through [`tracing`]: failures its threads carry on after, lost engines and
//! servers, and flows installed and removed. Nothing per input is logged. A Rust app installs any
//! `tracing` subscriber it likes; [`init`] installs a simple one, mainly for C apps.

use tracing_subscriber::EnvFilter;

use crate::{Error, Result};

/// What is logged when neither the caller nor `RUST_LOG` says: warnings and errors.
const DEFAULT_FILTER: &str = "warn";

/// Prints the log to standard error. `filter` picks what is printed, in the syntax of `RUST_LOG`,
/// e.g. `"info"` or `"gabriel=debug"`. With `None`, `RUST_LOG` picks, or warnings and errors are
/// printed if it is unset. Fails if the filter is malformed, or a subscriber is already installed.
pub fn init(filter: Option<&str>) -> Result<()> {
    let filter = match filter {
        Some(filter) => EnvFilter::try_new(filter)
            .map_err(|e| Error::InvalidArgument(format!("invalid log filter {filter:?}: {e}")))?,
        None => {
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(DEFAULT_FILTER))
        }
    };
    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .try_init()
        .map_err(|e| Error::InvalidArgument(format!("a logger is already installed: {e}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn malformed_filter_is_rejected() {
        assert!(matches!(
            init(Some("gabriel=loud")),
            Err(Error::InvalidArgument(_))
        ));
    }
}
