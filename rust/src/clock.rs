//! The clock of Gabriel's timestamps. Every event carries the time it happened, so an app can
//! tell how long its inputs waited and where.
//!
//! Timestamps are read from the monotonic clock (`CLOCK_MONOTONIC` on Linux), which every process
//! on a machine shares. So timestamps taken by the producer, the client, the server and the
//! engines on one machine can be compared, and an app can take its own with [`Timestamp::now`],
//! or with `clock_gettime(CLOCK_MONOTONIC)` in C. Timestamps from different machines cannot.

use std::time::Duration;

use iceoryx2::prelude::ZeroCopySend;
use rustix::time::{ClockId, clock_gettime};
use serde::{Deserialize, Serialize};

/// A moment on the machine's monotonic clock, in nanoseconds since an unspecified start, e.g.
/// boot. Unlike [`std::time::Instant`], it can be sent to other processes.
#[derive(
    Debug,
    Clone,
    Copy,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    Hash,
    Default,
    ZeroCopySend,
    Serialize,
    Deserialize,
)]
#[repr(C)]
pub struct Timestamp {
    nanos: u64,
}

impl Timestamp {
    /// The current time.
    pub fn now() -> Self {
        let duration = Duration::try_from(clock_gettime(ClockId::Monotonic))
            .expect("the monotonic clock is never negative");
        Self::from_nanos(
            u64::try_from(duration.as_nanos()).expect("the monotonic clock fits 584 years"),
        )
    }

    /// The moment `nanos` nanoseconds after the clock's start.
    pub fn from_nanos(nanos: u64) -> Self {
        Self { nanos }
    }

    /// Nanoseconds since the clock's start.
    pub fn as_nanos(&self) -> u64 {
        self.nanos
    }

    /// How long after `earlier` this moment is, or zero if it is not after it.
    pub fn saturating_duration_since(&self, earlier: Timestamp) -> Duration {
        Duration::from_nanos(self.nanos.saturating_sub(earlier.nanos))
    }
}

#[cfg(test)]
mod tests {
    use std::{thread, time::Instant};

    use super::*;

    const PAUSE: Duration = Duration::from_millis(5);

    #[test]
    fn time_passes_as_the_standard_clock_says() {
        let (start, std_start) = (Timestamp::now(), Instant::now());
        thread::sleep(PAUSE);
        let std_elapsed = std_start.elapsed();
        let elapsed = Timestamp::now().saturating_duration_since(start);

        assert!(elapsed >= PAUSE);
        // Both read the monotonic clock, so they differ only by the reads in between.
        assert!(elapsed.abs_diff(std_elapsed) < PAUSE);
    }

    #[test]
    fn earlier_moments_give_zero() {
        let earlier = Timestamp::from_nanos(1);

        assert_eq!(
            earlier.saturating_duration_since(Timestamp::from_nanos(2)),
            Duration::ZERO
        );
    }
}
