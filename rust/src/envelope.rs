//! Payload envelope: metadata carried in each sample's iceoryx2 user header.

use iceoryx2::prelude::ZeroCopySend;
use iceoryx2_bb_container::string::StaticString;

/// Maximum length of a flow name.
pub const MAX_FLOW_NAME: usize = 64;

#[derive(Debug, Default, Clone, Copy, ZeroCopySend)]
#[repr(C)]
pub struct Envelope {
    /// The producer's session, chosen randomly when it starts. With `seq`, it identifies the input.
    pub session: u64,
    /// Increases with every input the producer publishes.
    pub seq: u64,
    /// Number of valid bytes at the start of the payload.
    pub len: u64,
    /// Flow the input was published on.
    pub flow: StaticString<MAX_FLOW_NAME>,
}
