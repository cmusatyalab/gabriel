use iceoryx2::prelude::ZeroCopySend;
use iceoryx2_bb_container::string::{StaticString, String as _};
use serde::{Deserialize, Serialize};

/// Identifies an input edge within a flow.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, Default, ZeroCopySend, Serialize, Deserialize,
)]
#[repr(C)]
pub(crate) struct EdgeId(pub(crate) u32);

/// Maximum length of a producer name.
pub const MAX_PRODUCER_NAME: usize = 64;

/// A producer's name, with a fixed size so it can live in shared memory.
pub(crate) type ProducerName = StaticString<MAX_PRODUCER_NAME>;

/// Identifies one input of a producer.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, Default, ZeroCopySend, Serialize, Deserialize,
)]
#[repr(C)]
pub struct InputId {
    pub(crate) producer: ProducerName,
    pub(crate) session: u64,
    pub(crate) seq: u64,
}

impl InputId {
    /// The producer that published the input.
    pub fn producer(&self) -> &str {
        self.producer.as_str()
    }

    /// Chosen randomly when the producer started, so ids never repeat across restarts.
    pub fn session(&self) -> u64 {
        self.session
    }

    /// Counts the producer's inputs within its session, from 1.
    pub fn seq(&self) -> u64 {
        self.seq
    }
}

/// Identifies one offer of an input to an edge.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, Hash, Default, ZeroCopySend, Serialize, Deserialize,
)]
#[repr(C)]
pub(crate) struct OfferId {
    pub(crate) session: u64,
    pub(crate) edge: EdgeId,
    pub(crate) seq: u64,
}

/// Identifies a node within a flow.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub(crate) struct NodeId(pub(crate) u32);
