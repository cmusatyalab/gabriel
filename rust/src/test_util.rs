//! Helpers shared by the unit tests.

use std::time::Duration;

use uuid::Uuid;

use crate::Producer;

/// Long enough for a published input to arrive, short enough to keep tests fast.
pub(crate) const TIMEOUT: Duration = Duration::from_millis(200);
pub(crate) const MAX_INPUT_SIZE: usize = 16;
pub(crate) const INPUT_LEN: usize = 4;
pub(crate) const FLOW: &str = "flow";

/// iceoryx2 services are shared machine-wide, so every test uses fresh names to stay isolated
/// from parallel tests and other test runs.
pub(crate) fn unique(prefix: &str) -> String {
    format!("{prefix}-{}", Uuid::new_v4())
}

/// Publishes an input of `INPUT_LEN` bytes, all set to `value`.
pub(crate) fn publish(producer: &mut Producer, value: u8) {
    let buf = producer.new_input().unwrap();
    for byte in &mut buf[..INPUT_LEN] {
        byte.write(value);
    }
    producer.publish_input(INPUT_LEN, FLOW).unwrap();
}
