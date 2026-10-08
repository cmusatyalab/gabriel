use iceoryx2::prelude::ZeroCopySend;
use iceoryx2_bb_container::string::StaticString;

/// Identifies an input edge within a flow.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, ZeroCopySend)]
#[repr(C)]
pub(crate) struct EdgeId(pub(crate) u32);

/// Maximum length of a producer name.
pub const MAX_PRODUCER_NAME: usize = 64;

/// A producer's name, with a fixed size so it can live in shared memory.
pub(crate) type ProducerName = StaticString<MAX_PRODUCER_NAME>;

/// Identifies one input of a producer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, ZeroCopySend)]
#[repr(C)]
pub(crate) struct InputId {
    pub(crate) producer: ProducerName,
    pub(crate) session: u64,
    pub(crate) seq: u64,
}

/// Identifies one offer of an input to an edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, ZeroCopySend)]
#[repr(C)]
pub(crate) struct OfferId {
    pub(crate) session: u64,
    pub(crate) edge: EdgeId,
    pub(crate) seq: u64,
}

/// Identifies a node within a flow.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) struct NodeId(pub(crate) u32);
