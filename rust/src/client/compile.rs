//! Turns an app's flow spec into routing state for the client and for each server ("Installing a
//! flow" in the design note). An edge between two engines on one server belongs to that server;
//! every other edge belongs to the client, and a node's client edges to one server form a single
//! crossing. Only local engines are supported until the remote server exists.

use std::{collections::HashMap, time::Duration};

use petgraph::{
    algo::toposort,
    graph::{DiGraph, NodeIndex},
    visit::EdgeRef,
};

use crate::{
    Error, Result,
    ipc::types::EngineName,
    routing::{
        flow::EdgeSource,
        ids::{EdgeId, NodeId, ProducerName},
        server_router::{self, CrossingSpec, InstallFlow},
    },
    services::ENGINE_DELIVERY_LIMIT,
    spec::{FlowSpec, Location},
};

/// A flow, split into the client's part and the local server's part.
#[derive(Debug)]
pub(super) struct CompiledFlow {
    pub(super) producers: Vec<CompiledProducer>,
    /// The client's edges: one crossing per producer.
    pub(super) crossings: Vec<CompiledCrossing>,
    /// The node of each engine, so the client can tell which node an event is about.
    pub(super) nodes: HashMap<EngineName, NodeId>,
    /// The local server's part.
    pub(super) local: InstallFlow,
}

#[derive(Debug, PartialEq)]
pub(super) struct CompiledProducer {
    pub(super) name: ProducerName,
    pub(super) tokens: usize,
    /// How long an input may take along the longest path of the flow, after which its token
    /// returns even if offers are still open.
    pub(super) ticket_timeout: Duration,
}

/// A crossing the client owns.
#[derive(Debug, PartialEq)]
pub(super) struct CompiledCrossing {
    pub(super) id: EdgeId,
    pub(super) source: EdgeSource,
    /// The first of its target engines. The crossing goes to their server, which offers each input
    /// to every target.
    pub(super) target: NodeId,
    /// How long the crossing waits for the first of its targets to finish an input.
    pub(super) timeout: Duration,
}

/// The longest an input can take at an engine, from reaching the engine's server to its outcome:
/// the engine first finishes the inputs it holds, then takes this one. `timeout` bounds each.
fn engine_wait(timeout: Duration) -> Duration {
    let turns = u32::try_from(ENGINE_DELIVERY_LIMIT + 1).expect("the delivery limit is small");
    timeout * turns
}

/// Checks `spec`, and splits the flow `flow` between the client and the local server.
pub(super) fn compile(flow: &str, spec: &FlowSpec) -> Result<CompiledFlow> {
    // The nodes are the engines, then the producers. A node's index in the graph is its id.
    let mut graph = DiGraph::<&str, Duration>::new();
    let mut by_name = HashMap::new();
    for engine in &spec.engines {
        if engine.location == Location::Remote {
            return Err(Error::InvalidArgument(format!(
                "engine {:?} runs on the remote server, which is not supported yet",
                engine.name
            )));
        }
        engine_name(&engine.name)?;
        add_node(&mut graph, &mut by_name, &engine.name)?;
    }
    for producer in &spec.producers {
        producer_name(&producer.name)?;
        if producer.tokens == 0 {
            return Err(Error::InvalidArgument(format!(
                "producer {:?} needs at least 1 token",
                producer.name
            )));
        }
        add_node(&mut graph, &mut by_name, &producer.name)?;
    }

    let is_engine = |node: NodeIndex| node.index() < spec.engines.len();
    for edge in &spec.edges {
        let from = *by_name.get(edge.from.as_str()).ok_or_else(|| {
            Error::InvalidArgument(format!(
                "edge starts at {:?}, which is not in the flow",
                edge.from
            ))
        })?;
        let to = by_name
            .get(edge.to.as_str())
            .copied()
            .filter(|&to| is_engine(to))
            .ok_or_else(|| {
                Error::InvalidArgument(format!(
                    "edge leads to {:?}, which is not an engine of the flow",
                    edge.to
                ))
            })?;
        if graph.contains_edge(from, to) {
            return Err(Error::InvalidArgument(format!(
                "the edge from {:?} to {:?} appears twice",
                edge.from, edge.to
            )));
        }
        if edge.timeout.is_zero() {
            return Err(Error::InvalidArgument(format!(
                "the edge from {:?} to {:?} needs a timeout",
                edge.from, edge.to
            )));
        }
        // Edges are added in order, so an edge's index in the graph is its id.
        graph.add_edge(from, to, edge.timeout);
    }
    let order = toposort(&graph, None).map_err(|cycle| {
        Error::InvalidArgument(format!(
            "the input edges form a cycle through {:?}",
            graph[cycle.node_id()]
        ))
    })?;

    let server_edge =
        |edge: petgraph::graph::EdgeReference<'_, Duration>| server_router::EdgeSpec {
            id: edge_id(edge.id().index()),
            from: node_id(edge.source()),
            to: node_id(edge.target()),
            timeout: *edge.weight(),
        };
    let mut local = InstallFlow {
        flow: flow.to_owned(),
        engines: spec
            .engines
            .iter()
            .enumerate()
            .map(|(index, engine)| (node_id(NodeIndex::new(index)), engine.name.clone()))
            .collect(),
        // Edges between engines belong to the engines' server.
        edges: graph
            .edge_references()
            .filter(|edge| is_engine(edge.source()))
            .map(server_edge)
            .collect(),
        crossings: Vec::new(),
    };

    let mut producers = Vec::with_capacity(spec.producers.len());
    let mut crossings = Vec::with_capacity(spec.producers.len());
    for (offset, producer) in spec.producers.iter().enumerate() {
        let node = NodeIndex::new(spec.engines.len() + offset);
        let mut targets: Vec<_> = graph.edges(node).map(server_edge).collect();
        targets.sort_by_key(|target| target.id.0);
        let Some(first) = targets.first() else {
            return Err(Error::InvalidArgument(format!(
                "producer {:?} feeds no engine",
                producer.name
            )));
        };
        let name = producer_name(&producer.name)?;
        // Crossings are numbered after the edges.
        let id = edge_id(spec.edges.len() + offset);
        crossings.push(CompiledCrossing {
            id,
            source: EdgeSource::Producer(name),
            target: first.to,
            timeout: targets
                .iter()
                .map(|target| engine_wait(target.timeout))
                .max()
                .expect("a crossing has targets"),
        });
        local.crossings.push(CrossingSpec { id, targets });
        producers.push(CompiledProducer {
            name,
            tokens: producer.tokens,
            ticket_timeout: longest_wait(&graph, &order, node),
        });
    }

    let nodes = spec
        .engines
        .iter()
        .enumerate()
        .map(|(index, engine)| {
            let name = engine_name(&engine.name).expect("checked above");
            (name, node_id(NodeIndex::new(index)))
        })
        .collect();
    Ok(CompiledFlow {
        producers,
        crossings,
        nodes,
        local,
    })
}

/// Adds the node `name` to `graph`, unless the flow already has a node of that name.
fn add_node<'a>(
    graph: &mut DiGraph<&'a str, Duration>,
    by_name: &mut HashMap<&'a str, NodeIndex>,
    name: &'a str,
) -> Result<()> {
    if by_name.insert(name, graph.add_node(name)).is_some() {
        return Err(Error::InvalidArgument(format!(
            "{name:?} names two nodes of the flow"
        )));
    }
    Ok(())
}

/// The longest an input from the node `start` can spend in the flow: along each path, it waits at
/// every engine in turn. `order` is the graph's nodes in topological order.
fn longest_wait(
    graph: &DiGraph<&str, Duration>,
    order: &[NodeIndex],
    start: NodeIndex,
) -> Duration {
    let mut longest = HashMap::from([(start, Duration::ZERO)]);
    for &node in order {
        let Some(&waited) = longest.get(&node) else {
            continue;
        };
        for edge in graph.edges(node) {
            let arrived = waited + engine_wait(*edge.weight());
            longest
                .entry(edge.target())
                .and_modify(|longest: &mut Duration| *longest = (*longest).max(arrived))
                .or_insert(arrived);
        }
    }
    longest.into_values().max().unwrap_or_default()
}

fn node_id(node: NodeIndex) -> NodeId {
    NodeId(u32::try_from(node.index()).expect("a flow has fewer than 2^32 nodes"))
}

fn edge_id(index: usize) -> EdgeId {
    EdgeId(u32::try_from(index).expect("a flow has fewer than 2^32 edges"))
}

fn engine_name(name: &str) -> Result<EngineName> {
    if name.is_empty() {
        return Err(Error::InvalidArgument(String::from("engine name is empty")));
    }
    EngineName::try_from(name)
        .map_err(|_| Error::InvalidArgument(format!("engine name {name:?} is too long")))
}

fn producer_name(name: &str) -> Result<ProducerName> {
    if name.is_empty() {
        return Err(Error::InvalidArgument(String::from(
            "producer name is empty",
        )));
    }
    ProducerName::try_from(name)
        .map_err(|_| Error::InvalidArgument(format!("producer name {name:?} is too long")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::{EdgeSpec, EngineSpec, ProducerSpec};

    const FLOW: &str = "tracking";
    const TOKENS: usize = 2;
    const FILTER_TIMEOUT: Duration = Duration::from_millis(100);
    const DETECTOR_TIMEOUT: Duration = Duration::from_millis(300);

    fn engine(name: &str) -> EngineSpec {
        EngineSpec {
            name: String::from(name),
            location: Location::Local,
        }
    }

    fn edge(from: &str, to: &str, timeout: Duration) -> EdgeSpec {
        EdgeSpec {
            from: String::from(from),
            to: String::from(to),
            timeout,
        }
    }

    /// camera → filter → detector, all on the local server.
    fn cascade() -> FlowSpec {
        FlowSpec {
            producers: vec![ProducerSpec {
                name: String::from("camera"),
                tokens: TOKENS,
            }],
            engines: vec![engine("filter"), engine("detector")],
            edges: vec![
                edge("camera", "filter", FILTER_TIMEOUT),
                edge("filter", "detector", DETECTOR_TIMEOUT),
            ],
        }
    }

    fn rejects(spec: &FlowSpec) -> bool {
        matches!(compile(FLOW, spec), Err(Error::InvalidArgument(_)))
    }

    #[test]
    fn engine_edges_go_to_the_server_and_producer_edges_cross() {
        let compiled = compile(FLOW, &cascade()).unwrap();

        let (filter, detector, camera) = (NodeId(0), NodeId(1), NodeId(2));
        let (to_filter, to_detector, crossing) = (EdgeId(0), EdgeId(1), EdgeId(2));
        assert_eq!(
            compiled.local,
            InstallFlow {
                flow: String::from(FLOW),
                engines: vec![
                    (filter, String::from("filter")),
                    (detector, String::from("detector"))
                ],
                edges: vec![server_router::EdgeSpec {
                    id: to_detector,
                    from: filter,
                    to: detector,
                    timeout: DETECTOR_TIMEOUT,
                }],
                crossings: vec![CrossingSpec {
                    id: crossing,
                    targets: vec![server_router::EdgeSpec {
                        id: to_filter,
                        from: camera,
                        to: filter,
                        timeout: FILTER_TIMEOUT,
                    }],
                }],
            }
        );
        assert_eq!(
            compiled.crossings,
            vec![CompiledCrossing {
                id: crossing,
                source: EdgeSource::Producer(ProducerName::try_from("camera").unwrap()),
                target: filter,
                timeout: engine_wait(FILTER_TIMEOUT),
            }]
        );
        assert_eq!(
            compiled.nodes[&EngineName::try_from("detector").unwrap()],
            detector
        );
    }

    #[test]
    fn a_producers_edges_form_one_crossing() {
        let mut spec = cascade();
        spec.edges
            .push(edge("camera", "detector", DETECTOR_TIMEOUT));

        let compiled = compile(FLOW, &spec).unwrap();

        let [crossing] = &compiled.local.crossings[..] else {
            panic!("expected one crossing, got {:?}", compiled.local.crossings);
        };
        let targets: Vec<NodeId> = crossing.targets.iter().map(|target| target.to).collect();
        assert_eq!(targets, vec![NodeId(0), NodeId(1)]);
        // The crossing waits for its slowest target.
        assert_eq!(compiled.crossings[0].timeout, engine_wait(DETECTOR_TIMEOUT));
    }

    #[test]
    fn ticket_timeout_covers_the_longest_path() {
        let mut spec = cascade();
        spec.edges
            .push(edge("camera", "detector", DETECTOR_TIMEOUT));

        let compiled = compile(FLOW, &spec).unwrap();

        assert_eq!(
            compiled.producers,
            vec![CompiledProducer {
                name: ProducerName::try_from("camera").unwrap(),
                tokens: TOKENS,
                ticket_timeout: engine_wait(FILTER_TIMEOUT) + engine_wait(DETECTOR_TIMEOUT),
            }]
        );
    }

    #[test]
    fn rejects_a_cycle() {
        let mut spec = cascade();
        spec.edges.push(edge("detector", "filter", FILTER_TIMEOUT));

        assert!(rejects(&spec));
    }

    #[test]
    fn rejects_an_edge_to_a_producer_or_an_unknown_node() {
        let mut to_producer = cascade();
        to_producer
            .edges
            .push(edge("filter", "camera", FILTER_TIMEOUT));
        let mut from_unknown = cascade();
        from_unknown
            .edges
            .push(edge("lidar", "filter", FILTER_TIMEOUT));

        assert!(rejects(&to_producer));
        assert!(rejects(&from_unknown));
    }

    #[test]
    fn rejects_a_repeated_edge_or_name() {
        let mut repeated_edge = cascade();
        repeated_edge
            .edges
            .push(edge("camera", "filter", FILTER_TIMEOUT));
        let mut repeated_name = cascade();
        repeated_name.engines.push(engine("camera"));

        assert!(rejects(&repeated_edge));
        assert!(rejects(&repeated_name));
    }

    #[test]
    fn rejects_a_producer_without_tokens_or_edges() {
        let mut no_tokens = cascade();
        no_tokens.producers[0].tokens = 0;
        let mut no_edges = cascade();
        no_edges.producers.push(ProducerSpec {
            name: String::from("lidar"),
            tokens: TOKENS,
        });

        assert!(rejects(&no_tokens));
        assert!(rejects(&no_edges));
    }

    #[test]
    fn rejects_remote_engines_for_now() {
        let mut spec = cascade();
        spec.engines[1].location = Location::Remote;

        assert!(rejects(&spec));
    }
}
