//! Server: accepts inputs from clients and dispatches them to engines.

use crate::Result;

/// Dispatches inputs to a dynamic set of engines.
#[derive(Debug)]
pub struct Server {}

impl Server {
    /// Creates a server named `name` accepting remote clients on the network
    /// address `bind_addr`.
    pub fn new(name: &str, bind_addr: &str) -> Result<Self> {
        let _ = (name, bind_addr);
        todo!()
    }

    /// Starts dispatching to the local engine `name`.
    pub fn add_engine(&mut self, name: &str) -> Result<()> {
        let _ = name;
        todo!()
    }

    /// Stops dispatching to the engine `name`.
    pub fn remove_engine(&mut self, name: &str) -> Result<()> {
        let _ = name;
        todo!()
    }
}
