//! Helpers shared by the unit tests.

use std::{sync::Once, time::Duration};

use uuid::Uuid;

use crate::{InputId, Producer};

/// Long enough for a published input to arrive, short enough to keep tests fast.
pub(crate) const TIMEOUT: Duration = Duration::from_millis(200);
pub(crate) const MAX_INPUT_SIZE: usize = 16;
pub(crate) const INPUT_LEN: usize = 4;
pub(crate) const FLOW: &str = "flow";

/// iceoryx2 services are shared machine-wide, so every test uses fresh names to stay isolated
/// from parallel tests and other test runs. Every test that opens services calls this first, so
/// it also raises the process's limit on open files (see [`raise_file_limit`]).
pub(crate) fn unique(prefix: &str) -> String {
    raise_file_limit();
    format!("{prefix}-{}", Uuid::new_v4())
}

/// Raises the soft limit on open files to the hard limit, once. Each iceoryx2 port holds a few
/// files, and the tests run in parallel in one process, so together they need more than the
/// common default of 1024. Raising the soft limit needs no privileges.
fn raise_file_limit() {
    static RAISED: Once = Once::new();
    RAISED.call_once(|| {
        if let Err(e) = rlimit::increase_nofile_limit(u64::MAX) {
            eprintln!("failed to raise the limit on open files: {e}");
        }
    });
}

/// Publishes an input of `INPUT_LEN` bytes, all set to `value`, and returns its id.
pub(crate) fn publish(producer: &mut Producer, value: u8) -> InputId {
    let buf = producer.new_input().unwrap();
    for byte in &mut buf[..INPUT_LEN] {
        byte.write(value);
    }
    producer.publish_input(INPUT_LEN, FLOW).unwrap()
}
