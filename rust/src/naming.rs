//! iceoryx2 service naming scheme. Local peers find each other by name alone:
//! a producer named `driver` publishes on `gabriel/producer/driver/input` and
//! signals `gabriel/producer/driver/ready`.

use iceoryx2::prelude::{NodeName, ServiceName};

use crate::{Error, Result};

/// Prefix shared by every Gabriel service.
const ROOT: &str = "gabriel";
/// Separator between the segments of a service name.
const SEPARATOR: char = '/';
/// Segment for producer services.
const PRODUCER: &str = "producer";
/// Segment for engine services and nodes.
const ENGINE: &str = "engine";
/// Segment for server nodes.
const SERVER: &str = "server";
/// Service carrying a component's inputs.
const INPUT: &str = "input";
/// Service carrying a component's events.
const EVENTS: &str = "events";
/// Service carrying an engine's outcomes to its server.
const OUTCOME: &str = "outcome";
/// Event signalled when a producer has published a new input.
const READY: &str = "ready";
/// Event signalled when a server has delivered an input to an engine.
const INPUT_READY: &str = "input-ready";
/// Event signalled when an engine has reported an outcome to its server.
const OUTCOME_READY: &str = "outcome-ready";

/// Service name of the producer `name`'s input.
pub fn producer_input_service(name: &str) -> Result<ServiceName> {
    service(PRODUCER, name, INPUT)
}

/// Service name of the producer `name`'s ready event.
pub fn producer_ready_service(name: &str) -> Result<ServiceName> {
    service(PRODUCER, name, READY)
}

/// Service name of the engine `name`'s input.
pub fn engine_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, INPUT)
}

/// Service name of the engine `name`'s events.
pub fn engine_events_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, EVENTS)
}

/// Service name of the engine `name`'s outcomes.
pub fn engine_outcome_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, OUTCOME)
}

/// Service name of the event the engine `name`'s server signals after delivering an input.
pub fn engine_input_ready_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, INPUT_READY)
}

/// Service name of the event the engine `name` signals after reporting an outcome.
pub fn engine_outcome_ready_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, OUTCOME_READY)
}

/// Node name of the engine `name`. Its server finds the engine's process by it.
pub fn engine_node(name: &str) -> Result<NodeName> {
    node(ENGINE, name)
}

/// The engine whose node is named `node`, if it is an engine's node.
pub fn engine_of_node(node: &str) -> Option<&str> {
    node.strip_prefix(ROOT)?
        .strip_prefix(SEPARATOR)?
        .strip_prefix(ENGINE)?
        .strip_prefix(SEPARATOR)
}

/// Node name of the server `name`.
pub fn server_node(name: &str) -> Result<NodeName> {
    node(SERVER, name)
}

fn service(kind: &str, name: &str, suffix: &str) -> Result<ServiceName> {
    let service_name = format!("{}{SEPARATOR}{suffix}", prefix(kind, name)?);
    ServiceName::new(&service_name)
        .map_err(|e| Error::InvalidArgument(format!("invalid service name {service_name:?}: {e}")))
}

fn node(kind: &str, name: &str) -> Result<NodeName> {
    let node_name = prefix(kind, name)?;
    NodeName::new(&node_name)
        .map_err(|e| Error::InvalidArgument(format!("invalid node name {node_name:?}: {e}")))
}

/// `gabriel/<kind>/<name>`, after checking `name`.
fn prefix(kind: &str, name: &str) -> Result<String> {
    if name.is_empty() || name.contains(SEPARATOR) {
        return Err(Error::InvalidArgument(format!(
            "{kind} name {name:?} must be non-empty and contain no '{SEPARATOR}'"
        )));
    }
    Ok(format!("{ROOT}{SEPARATOR}{kind}{SEPARATOR}{name}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn producer_name() {
        assert_eq!(
            producer_input_service("cam0").unwrap().as_str(),
            "gabriel/producer/cam0/input"
        );
    }

    #[test]
    fn producer_ready_name() {
        assert_eq!(
            producer_ready_service("cam0").unwrap().as_str(),
            "gabriel/producer/cam0/ready"
        );
    }

    #[test]
    fn engine_name() {
        assert_eq!(
            engine_service("yolo").unwrap().as_str(),
            "gabriel/engine/yolo/input"
        );
    }

    #[test]
    fn engine_events_name() {
        assert_eq!(
            engine_events_service("yolo").unwrap().as_str(),
            "gabriel/engine/yolo/events"
        );
    }

    #[test]
    fn engine_outcome_name() {
        assert_eq!(
            engine_outcome_service("yolo").unwrap().as_str(),
            "gabriel/engine/yolo/outcome"
        );
    }

    #[test]
    fn engine_ready_names() {
        assert_eq!(
            engine_input_ready_service("yolo").unwrap().as_str(),
            "gabriel/engine/yolo/input-ready"
        );
        assert_eq!(
            engine_outcome_ready_service("yolo").unwrap().as_str(),
            "gabriel/engine/yolo/outcome-ready"
        );
    }

    #[test]
    fn engine_node_name_names_its_engine() {
        let node = engine_node("yolo").unwrap();

        assert_eq!(node.as_str(), "gabriel/engine/yolo");
        assert_eq!(engine_of_node(node.as_str()), Some("yolo"));
        assert_eq!(engine_of_node(server_node("yolo").unwrap().as_str()), None);
        assert_eq!(engine_of_node("yolo"), None);
    }

    #[test]
    fn rejects_empty_name() {
        assert!(matches!(
            producer_input_service(""),
            Err(Error::InvalidArgument(_))
        ));
    }

    #[test]
    fn rejects_separator_in_name() {
        assert!(matches!(
            engine_service("a/b"),
            Err(Error::InvalidArgument(_))
        ));
    }
}
