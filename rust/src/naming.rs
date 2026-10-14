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
/// Segment for server services and nodes.
const SERVER: &str = "server";
/// Segment for client services and nodes.
const CLIENT: &str = "client";
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
/// Service carrying a server's control calls.
const CONTROL: &str = "control";
/// Event signalled when a server has published a client's or an engine's events.
const EVENTS_READY: &str = "events-ready";
/// Service carrying the events about a producer's inputs, for the producer's app.
const RESULT: &str = "result";
/// Event signalled when a client has published events about a producer's inputs.
const RESULT_READY: &str = "result-ready";
/// Event signalled by a process's network thread when it has received something.
const REMOTE_READY: &str = "remote-ready";

/// Service name of the producer `name`'s input.
pub fn producer_input_service(name: &str) -> Result<ServiceName> {
    service(PRODUCER, name, INPUT)
}

/// Service name of the producer `name`'s ready event.
pub fn producer_ready_service(name: &str) -> Result<ServiceName> {
    service(PRODUCER, name, READY)
}

/// Service name of the events about the producer `name`'s inputs.
pub fn producer_result_service(name: &str) -> Result<ServiceName> {
    service(PRODUCER, name, RESULT)
}

/// Service name of the event signalled after events about the producer `name`'s inputs.
pub fn producer_result_ready_service(name: &str) -> Result<ServiceName> {
    service(PRODUCER, name, RESULT_READY)
}

/// Service name of the engine `name`'s input.
pub fn engine_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, INPUT)
}

/// Service name of the engine `name`'s events.
pub fn engine_events_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, EVENTS)
}

/// Service name of the event the engine `name`'s server signals after publishing its events.
pub fn engine_events_ready_service(name: &str) -> Result<ServiceName> {
    service(ENGINE, name, EVENTS_READY)
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
    name_of_node(ENGINE, node)
}

/// Node name of the server `name`.
pub fn server_node(name: &str) -> Result<NodeName> {
    node(SERVER, name)
}

/// Service name of the server `name`'s control calls, such as installing a flow.
pub fn server_control_service(name: &str) -> Result<ServiceName> {
    service(SERVER, name, CONTROL)
}

/// Service name of the inputs local clients send to the server `name`.
pub fn server_input_service(name: &str) -> Result<ServiceName> {
    service(SERVER, name, INPUT)
}

/// Service name of the event a local client signals after sending the server `name` an input.
pub fn server_input_ready_service(name: &str) -> Result<ServiceName> {
    service(SERVER, name, INPUT_READY)
}

/// Service name of the events the local server sends the client `name`, about its flows.
pub fn client_events_service(name: &str) -> Result<ServiceName> {
    service(CLIENT, name, EVENTS)
}

/// Service name of the event the local server signals after sending the client `name` events.
pub fn client_events_ready_service(name: &str) -> Result<ServiceName> {
    service(CLIENT, name, EVENTS_READY)
}

/// Service name of the event the server `name`'s network thread signals after receiving calls or
/// inputs from remote clients.
pub fn server_remote_ready_service(name: &str) -> Result<ServiceName> {
    service(SERVER, name, REMOTE_READY)
}

/// Service name of the event the client `name`'s network thread signals after receiving events
/// from the remote server.
pub fn client_remote_ready_service(name: &str) -> Result<ServiceName> {
    service(CLIENT, name, REMOTE_READY)
}

/// The server whose node is named `node`, if it is a server's node.
pub fn server_of_node(node: &str) -> Option<&str> {
    name_of_node(SERVER, node)
}

/// Node name of the client `name`.
pub fn client_node(name: &str) -> Result<NodeName> {
    node(CLIENT, name)
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

/// The `<name>` of a node named `gabriel/<kind>/<name>`.
fn name_of_node<'a>(kind: &str, node: &'a str) -> Option<&'a str> {
    node.strip_prefix(ROOT)?
        .strip_prefix(SEPARATOR)?
        .strip_prefix(kind)?
        .strip_prefix(SEPARATOR)
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
    fn producer_result_names() {
        assert_eq!(
            producer_result_service("cam0").unwrap().as_str(),
            "gabriel/producer/cam0/result"
        );
        assert_eq!(
            producer_result_ready_service("cam0").unwrap().as_str(),
            "gabriel/producer/cam0/result-ready"
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
        assert_eq!(
            engine_events_ready_service("yolo").unwrap().as_str(),
            "gabriel/engine/yolo/events-ready"
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
    fn server_node_name_names_its_server() {
        let node = server_node("edge").unwrap();

        assert_eq!(server_of_node(node.as_str()), Some("edge"));
        assert_eq!(server_of_node(engine_node("edge").unwrap().as_str()), None);
    }

    #[test]
    fn server_names() {
        assert_eq!(
            server_control_service("edge").unwrap().as_str(),
            "gabriel/server/edge/control"
        );
        assert_eq!(
            server_input_service("edge").unwrap().as_str(),
            "gabriel/server/edge/input"
        );
        assert_eq!(
            server_input_ready_service("edge").unwrap().as_str(),
            "gabriel/server/edge/input-ready"
        );
    }

    #[test]
    fn client_names() {
        assert_eq!(
            client_events_service("app").unwrap().as_str(),
            "gabriel/client/app/events"
        );
        assert_eq!(
            client_events_ready_service("app").unwrap().as_str(),
            "gabriel/client/app/events-ready"
        );
        assert_eq!(client_node("app").unwrap().as_str(), "gabriel/client/app");
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
