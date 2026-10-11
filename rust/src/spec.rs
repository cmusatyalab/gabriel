//! How an app describes a flow: its producers, its engines and where each runs, and the input
//! edges between them. The app never says who owns an edge; the client works that out when it
//! adds the flow ([`Client::add_flow`](crate::Client::add_flow)).
//!
//! A spec can also be written in TOML, as a list of edges, and parsed with [`str::parse`]:
//!
//! ```toml
//! [[producers]]
//! name = "camera"
//! tokens = 2
//!
//! [[engines]]
//! name = "filter"
//! location = "local"
//!
//! [[engines]]
//! name = "detector"
//! location = "local"
//!
//! [[edges]]
//! from = "camera"
//! to = "filter"
//! timeout = "100ms"
//!
//! [[edges]]
//! from = "filter"
//! to = "detector"
//! timeout = "300ms"
//! ```

use std::{str::FromStr, time::Duration};

use serde::{Deserialize, Serialize};

use crate::Error;

/// A flow, as the app describes it.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FlowSpec {
    #[serde(default)]
    pub producers: Vec<ProducerSpec>,
    #[serde(default)]
    pub engines: Vec<EngineSpec>,
    #[serde(default)]
    pub edges: Vec<EdgeSpec>,
}

/// A producer that feeds the flow.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProducerSpec {
    /// The producer's name. The client must take its inputs
    /// ([`Client::add_producer`](crate::Client::add_producer)).
    pub name: String,
    /// How many of the producer's inputs may be in the flow at once.
    pub tokens: usize,
}

/// Where an engine runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Location {
    /// On the local server, on this machine.
    Local,
    /// On the remote server, e.g. a cloudlet.
    Remote,
}

/// An engine of the flow, by the name it registered with its server.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EngineSpec {
    pub name: String,
    pub location: Location,
}

/// An input edge: the inputs of a producer, or what an engine forwards, go to an engine.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EdgeSpec {
    /// The producer or engine the edge starts at.
    pub from: String,
    /// The engine the edge leads to.
    pub to: String,
    /// How long the engine at `to` may take to answer an input it was given. In TOML, a string
    /// such as `"300ms"` or `"2s"`.
    #[serde(with = "humantime_serde")]
    pub timeout: Duration,
}

impl FromStr for FlowSpec {
    type Err = Error;

    /// Parses a spec written in TOML. Only the syntax is checked here; adding the flow checks the
    /// graph.
    fn from_str(toml: &str) -> Result<Self, Self::Err> {
        toml::from_str(toml).map_err(|e| Error::InvalidArgument(format!("invalid flow spec: {e}")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rejects(toml: &str) -> bool {
        matches!(toml.parse::<FlowSpec>(), Err(Error::InvalidArgument(_)))
    }

    #[test]
    fn toml_spec_is_parsed() {
        let spec: FlowSpec = r#"
            [[producers]]
            name = "camera"
            tokens = 2

            [[engines]]
            name = "detector"
            location = "local"

            [[edges]]
            from = "camera"
            to = "detector"
            timeout = "300ms"
        "#
        .parse()
        .unwrap();

        assert_eq!(
            spec,
            FlowSpec {
                producers: vec![ProducerSpec {
                    name: String::from("camera"),
                    tokens: 2,
                }],
                engines: vec![EngineSpec {
                    name: String::from("detector"),
                    location: Location::Local,
                }],
                edges: vec![EdgeSpec {
                    from: String::from("camera"),
                    to: String::from("detector"),
                    timeout: Duration::from_millis(300),
                }],
            }
        );
    }

    #[test]
    fn spec_survives_writing_and_reading() {
        let spec: FlowSpec = r#"
            [[engines]]
            name = "detector"
            location = "remote"

            [[edges]]
            from = "camera"
            to = "detector"
            timeout = "1s 500ms"
        "#
        .parse()
        .unwrap();

        let written = toml::to_string(&spec).unwrap();

        assert_eq!(written.parse::<FlowSpec>().unwrap(), spec);
    }

    #[test]
    fn misspelled_field_is_rejected() {
        assert!(rejects(
            r#"
            [[producers]]
            name = "camera"
            token = 2
            "#
        ));
    }

    #[test]
    fn timeout_without_a_unit_is_rejected() {
        assert!(rejects(
            r#"
            [[edges]]
            from = "camera"
            to = "detector"
            timeout = "300"
            "#
        ));
    }

    #[test]
    fn unknown_location_is_rejected() {
        assert!(rejects(
            r#"
            [[engines]]
            name = "detector"
            location = "cloud"
            "#
        ));
    }
}
