//! iceoryx2 service naming scheme. Local peers find each other by name alone:
//! a producer named `driver` publishes on `gabriel/producer/driver/input` and
//! signals `gabriel/producer/driver/ready`.

use iceoryx2::prelude::ServiceName;

use crate::{Error, Result};

/// Prefix shared by every Gabriel service.
const ROOT: &str = "gabriel";
/// Separator between the segments of a service name.
const SEPARATOR: char = '/';
/// Segment for producer services.
const PRODUCER: &str = "producer";
/// Segment for engine services.
const ENGINE: &str = "engine";
/// Service carrying a component's inputs.
const INPUT: &str = "input";
/// Event signalled when a producer has published a new input.
const READY: &str = "ready";

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

fn service(kind: &str, name: &str, suffix: &str) -> Result<ServiceName> {
    if name.is_empty() || name.contains(SEPARATOR) {
        return Err(Error::InvalidArgument(format!(
            "{kind} name {name:?} must be non-empty and contain no '{SEPARATOR}'"
        )));
    }
    let service_name = format!("{ROOT}{SEPARATOR}{kind}{SEPARATOR}{name}{SEPARATOR}{suffix}");
    ServiceName::new(&service_name)
        .map_err(|e| Error::InvalidArgument(format!("invalid service name {service_name:?}: {e}")))
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
