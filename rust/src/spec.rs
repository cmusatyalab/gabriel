//! How an app describes a flow: its producers, its engines and where each runs, and the input
//! edges between them. The app never says who owns an edge; the client works that out when it
//! adds the flow ([`Client::add_flow`](crate::Client::add_flow)).

use std::time::Duration;

/// A flow, as the app describes it.
#[derive(Debug, Clone, Default)]
pub struct FlowSpec {
    pub producers: Vec<ProducerSpec>,
    pub engines: Vec<EngineSpec>,
    pub edges: Vec<EdgeSpec>,
}

/// A producer that feeds the flow.
#[derive(Debug, Clone)]
pub struct ProducerSpec {
    /// The producer's name. The client must take its inputs ([`Client::add_producer`](crate::Client::add_producer)).
    pub name: String,
    /// How many of the producer's inputs may be in the flow at once.
    pub tokens: usize,
}

/// Where an engine runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Location {
    /// On the local server, on this machine.
    Local,
    /// On the remote server, e.g. a cloudlet. Not supported yet.
    Remote,
}

/// An engine of the flow, by the name it registered with its server.
#[derive(Debug, Clone)]
pub struct EngineSpec {
    pub name: String,
    pub location: Location,
}

/// An input edge: the inputs of a producer, or what an engine forwards, go to an engine.
#[derive(Debug, Clone)]
pub struct EdgeSpec {
    /// The producer or engine the edge starts at.
    pub from: String,
    /// The engine the edge leads to.
    pub to: String,
    /// How long the engine at `to` may take to answer an input it was given.
    pub timeout: Duration,
}
