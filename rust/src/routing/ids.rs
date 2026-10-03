/// Identifies an input edge within a flow.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) struct EdgeId(pub(crate) u32);

/// Identifies one input of a producer.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct InputId {
    pub(crate) producer: String,
    pub(crate) session: u64,
    pub(crate) seq: u64,
}

/// Identifies one offer of an input to an edge.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) struct OfferId {
    pub(crate) session: u64,
    pub(crate) edge: EdgeId,
    pub(crate) seq: u64,
}
