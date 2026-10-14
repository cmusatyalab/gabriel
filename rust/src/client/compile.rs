//! Turns an app's flow spec into routing state for the client and for each server ("Installing a
//! flow" in the design note). An edge between two engines on one server belongs to that server;
//! every other edge belongs to the client, and a node's client edges to one server form a single
//! crossing. Inputs only move from the device to the remote server, so no edge may lead from a
//! remote engine to a local one.

use std::{collections::HashMap, time::Duration};

use petgraph::{
    algo::toposort,
    graph::{DiGraph, EdgeReference, NodeIndex},
    visit::{Dfs, EdgeRef},
};
use tracing::warn;

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

/// How long a round trip to the remote server may take, on top of the engines' own time. Generous,
/// because a queue on the link can multiply the round trip.
pub(super) const REMOTE_RTT_BOUND: Duration = Duration::from_secs(1);

/// The token left over when every place that can hold one of a producer's inputs holds one, so
/// that a newer input can still replace a waiting one ("Choosing C" in the design note).
const SPARE_TOKENS: usize = 1;

/// A flow, split into the client's part and a part for each server that runs its engines.
#[derive(Debug)]
pub(super) struct CompiledFlow {
    pub(super) producers: Vec<CompiledProducer>,
    /// The client's edges, grouped into crossings: one per producer or local engine and server.
    pub(super) crossings: Vec<CompiledCrossing>,
    /// The node of each engine, so the client can tell which node an event is about.
    pub(super) nodes: HashMap<EngineName, NodeId>,
    /// The part of each server that runs some of the flow's engines.
    pub(super) parts: HashMap<Location, InstallFlow>,
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
    /// The server its targets run on.
    pub(super) location: Location,
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

/// The longest an input can take from being sent to an engine at `location` to its outcome: the
/// wait at the engine, and the round trip to the remote server.
fn wait_at(location: Location, timeout: Duration) -> Duration {
    match location {
        Location::Local => engine_wait(timeout),
        Location::Remote => engine_wait(timeout) + REMOTE_RTT_BOUND,
    }
}

/// Checks `spec`, and splits the flow `flow` between the client and the servers. Each crossing to
/// the remote server may have `remote_crossing_capacity` inputs in flight.
pub(super) fn compile(
    flow: &str,
    spec: &FlowSpec,
    remote_crossing_capacity: usize,
) -> Result<CompiledFlow> {
    // The nodes are the engines, then the producers. A node's index in the graph is its id.
    let mut graph = DiGraph::<&str, Duration>::new();
    let mut by_name = HashMap::new();
    for engine in &spec.engines {
        engine_name(&engine.name)?;
        add_node(&mut graph, &mut by_name, &engine.name)?;
    }
    for producer in &spec.producers {
        producer_name(&producer.name)?;
        if producer.tokens == Some(0) {
            return Err(Error::InvalidArgument(format!(
                "producer {:?} needs at least 1 token",
                producer.name
            )));
        }
        add_node(&mut graph, &mut by_name, &producer.name)?;
    }

    // Where a node runs: producers are on the device, with the client.
    let location = |node: NodeIndex| spec.engines.get(node.index()).map(|engine| engine.location);
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
            .filter(|&to| location(to).is_some())
            .ok_or_else(|| {
                Error::InvalidArgument(format!(
                    "edge leads to {:?}, which is not an engine of the flow",
                    edge.to
                ))
            })?;
        if location(from) == Some(Location::Remote) && location(to) == Some(Location::Local) {
            return Err(Error::InvalidArgument(format!(
                "the edge from {:?} to {:?} leads from the remote server back to the device; \
                 only results come back",
                edge.from, edge.to
            )));
        }
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

    let target_location =
        |edge: EdgeReference<'_, Duration>| location(edge.target()).expect("edges lead to engines");
    // An edge between two engines on one server belongs to that server.
    let server_owned = |edge: &EdgeReference<'_, Duration>| {
        location(edge.source()) == Some(target_location(*edge))
    };
    let server_edge = |edge: EdgeReference<'_, Duration>| server_router::EdgeSpec {
        id: edge_id(edge.id().index()),
        from: node_id(edge.source()),
        to: node_id(edge.target()),
        timeout: *edge.weight(),
    };
    let mut parts: HashMap<Location, InstallFlow> = HashMap::new();
    for (index, engine) in spec.engines.iter().enumerate() {
        parts
            .entry(engine.location)
            .or_insert_with(|| InstallFlow {
                flow: flow.to_owned(),
                engines: Vec::new(),
                edges: Vec::new(),
                crossings: Vec::new(),
                client_sources: Vec::new(),
            })
            .engines
            .push((node_id(NodeIndex::new(index)), engine.name.clone()));
    }
    for edge in graph.edge_references().filter(server_owned) {
        let part = parts
            .get_mut(&target_location(edge))
            .expect("an engine's server has a part");
        part.edges.push(server_edge(edge));
    }

    // Every other edge belongs to the client. A node's client edges to one server form one
    // crossing, numbered after the edges.
    let mut crossings = Vec::new();
    let mut next_crossing = spec.edges.len();
    for node in graph.node_indices() {
        let source = match location(node) {
            Some(Location::Remote) => continue,
            Some(Location::Local) => EdgeSource::Engine(node_id(node)),
            None => EdgeSource::Producer(producer_name(graph[node])?),
        };
        for server in [Location::Local, Location::Remote] {
            let mut targets: Vec<_> = graph
                .edges(node)
                .filter(|edge| !server_owned(edge) && target_location(*edge) == server)
                .map(server_edge)
                .collect();
            targets.sort_by_key(|target| target.id.0);
            let Some(first) = targets.first() else {
                continue;
            };
            let id = edge_id(next_crossing);
            next_crossing += 1;
            crossings.push(CompiledCrossing {
                id,
                source: source.clone(),
                location: server,
                target: first.to,
                timeout: targets
                    .iter()
                    .map(|target| wait_at(server, target.timeout))
                    .max()
                    .expect("a crossing has targets"),
            });
            parts
                .get_mut(&server)
                .expect("an engine's server has a part")
                .crossings
                .push(CrossingSpec { id, targets });
        }
        // The local server sends the client what such an engine emits, for its crossings.
        if let EdgeSource::Engine(node) = source
            && crossings
                .last()
                .is_some_and(|crossing| crossing.source == source)
        {
            parts
                .get_mut(&Location::Local)
                .expect("a local engine's server has a part")
                .client_sources
                .push(node);
        }
    }

    // The wait an edge adds to an input's path: a round trip if it crosses to the remote server.
    let edge_wait = |edge: EdgeReference<'_, Duration>| {
        if server_owned(&edge) {
            engine_wait(*edge.weight())
        } else {
            wait_at(target_location(edge), *edge.weight())
        }
    };
    let mut producers = Vec::with_capacity(spec.producers.len());
    for (offset, producer) in spec.producers.iter().enumerate() {
        let node = NodeIndex::new(spec.engines.len() + offset);
        if graph.edges(node).next().is_none() {
            return Err(Error::InvalidArgument(format!(
                "producer {:?} feeds no engine",
                producer.name
            )));
        }
        let bound = token_bound(&graph, node, location, remote_crossing_capacity);
        let tokens = match producer.tokens {
            Some(tokens) if tokens < bound => {
                warn!(
                    producer = producer.name,
                    tokens,
                    bound,
                    "fewer tokens than places that can hold an input, so a waiting input can \
                     go stale"
                );
                tokens
            }
            Some(tokens) => tokens,
            None => bound,
        };
        producers.push(CompiledProducer {
            name: producer_name(&producer.name)?,
            tokens,
            ticket_timeout: longest_wait(&graph, &order, node, edge_wait),
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
        parts,
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

/// The token count of "Choosing C" in the design note, for the producer at `start`: one for each
/// engine on its paths, for the input in work there; one for each edge on them, for the input
/// waiting in its pending slot; `remote_crossing_capacity` for each crossing to the remote
/// server, for the inputs on the network; and a spare. `location` gives where an engine runs, and
/// `None` for a producer.
fn token_bound(
    graph: &DiGraph<&str, Duration>,
    start: NodeIndex,
    location: impl Fn(NodeIndex) -> Option<Location>,
    remote_crossing_capacity: usize,
) -> usize {
    let mut places = SPARE_TOKENS;
    let mut dfs = Dfs::new(graph, start);
    while let Some(node) = dfs.next(graph) {
        if node != start {
            places += 1;
        }
        places += graph.edges(node).count();
        // A node's edges from the device to the remote server form one crossing.
        let crosses = location(node) != Some(Location::Remote)
            && graph
                .edges(node)
                .any(|edge| location(edge.target()) == Some(Location::Remote));
        if crosses {
            places += remote_crossing_capacity;
        }
    }
    places
}

/// The longest an input from the node `start` can spend in the flow: along each path, it waits
/// `edge_wait` on every edge in turn. `order` is the graph's nodes in topological order.
fn longest_wait(
    graph: &DiGraph<&str, Duration>,
    order: &[NodeIndex],
    start: NodeIndex,
    edge_wait: impl Fn(EdgeReference<'_, Duration>) -> Duration,
) -> Duration {
    let mut longest = HashMap::from([(start, Duration::ZERO)]);
    for &node in order {
        let Some(&waited) = longest.get(&node) else {
            continue;
        };
        for edge in graph.edges(node) {
            let arrived = waited + edge_wait(edge);
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
    const REMOTE_CROSSING_CAPACITY: usize = 3;
    const FILTER_TIMEOUT: Duration = Duration::from_millis(100);
    const DETECTOR_TIMEOUT: Duration = Duration::from_millis(300);
    const TRACKER_TIMEOUT: Duration = Duration::from_millis(50);

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
                tokens: Some(TOKENS),
            }],
            engines: vec![engine("filter"), engine("detector")],
            edges: vec![
                edge("camera", "filter", FILTER_TIMEOUT),
                edge("filter", "detector", DETECTOR_TIMEOUT),
            ],
        }
    }

    fn rejects(spec: &FlowSpec) -> bool {
        matches!(
            compile(FLOW, spec, REMOTE_CROSSING_CAPACITY),
            Err(Error::InvalidArgument(_))
        )
    }

    #[test]
    fn engine_edges_go_to_the_server_and_producer_edges_cross() {
        let compiled = compile(FLOW, &cascade(), REMOTE_CROSSING_CAPACITY).unwrap();

        let (filter, detector, camera) = (NodeId(0), NodeId(1), NodeId(2));
        let (to_filter, to_detector, crossing) = (EdgeId(0), EdgeId(1), EdgeId(2));
        assert!(!compiled.parts.contains_key(&Location::Remote));
        assert_eq!(
            compiled.parts[&Location::Local],
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
                client_sources: vec![],
            }
        );
        assert_eq!(
            compiled.crossings,
            vec![CompiledCrossing {
                id: crossing,
                source: EdgeSource::Producer(ProducerName::try_from("camera").unwrap()),
                location: Location::Local,
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

        let compiled = compile(FLOW, &spec, REMOTE_CROSSING_CAPACITY).unwrap();

        let local = &compiled.parts[&Location::Local];
        let [crossing] = &local.crossings[..] else {
            panic!("expected one crossing, got {:?}", local.crossings);
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

        let compiled = compile(FLOW, &spec, REMOTE_CROSSING_CAPACITY).unwrap();

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
        no_tokens.producers[0].tokens = Some(0);
        let mut no_edges = cascade();
        no_edges.producers.push(ProducerSpec {
            name: String::from("lidar"),
            tokens: Some(TOKENS),
        });

        assert!(rejects(&no_tokens));
        assert!(rejects(&no_edges));
    }

    /// camera → filter (local) → detector (remote) → tracker (remote).
    fn offload() -> FlowSpec {
        let mut spec = cascade();
        spec.engines[1].location = Location::Remote;
        spec.engines.push(EngineSpec {
            name: String::from("tracker"),
            location: Location::Remote,
        });
        spec.edges
            .push(edge("detector", "tracker", TRACKER_TIMEOUT));
        spec
    }

    #[test]
    fn remote_engines_get_their_own_part_and_an_uplink() {
        let compiled = compile(FLOW, &offload(), REMOTE_CROSSING_CAPACITY).unwrap();

        let (filter, detector, tracker, camera) = (NodeId(0), NodeId(1), NodeId(2), NodeId(3));
        let (to_filter, to_detector, to_tracker) = (EdgeId(0), EdgeId(1), EdgeId(2));
        // Crossings are numbered after the edges, in the order of their source nodes.
        let (uplink, from_camera) = (EdgeId(3), EdgeId(4));
        assert_eq!(
            compiled.parts[&Location::Local],
            InstallFlow {
                flow: String::from(FLOW),
                engines: vec![(filter, String::from("filter"))],
                edges: vec![],
                crossings: vec![CrossingSpec {
                    id: from_camera,
                    targets: vec![server_router::EdgeSpec {
                        id: to_filter,
                        from: camera,
                        to: filter,
                        timeout: FILTER_TIMEOUT,
                    }],
                }],
                client_sources: vec![filter],
            }
        );
        assert_eq!(
            compiled.parts[&Location::Remote],
            InstallFlow {
                flow: String::from(FLOW),
                engines: vec![
                    (detector, String::from("detector")),
                    (tracker, String::from("tracker"))
                ],
                edges: vec![server_router::EdgeSpec {
                    id: to_tracker,
                    from: detector,
                    to: tracker,
                    timeout: TRACKER_TIMEOUT,
                }],
                crossings: vec![CrossingSpec {
                    id: uplink,
                    targets: vec![server_router::EdgeSpec {
                        id: to_detector,
                        from: filter,
                        to: detector,
                        timeout: DETECTOR_TIMEOUT,
                    }],
                }],
                client_sources: vec![],
            }
        );
        assert_eq!(
            compiled.crossings[0],
            CompiledCrossing {
                id: uplink,
                source: EdgeSource::Engine(filter),
                location: Location::Remote,
                target: detector,
                // An uplink also waits for the round trip.
                timeout: engine_wait(DETECTOR_TIMEOUT) + REMOTE_RTT_BOUND,
            }
        );
    }

    #[test]
    fn ticket_timeout_includes_the_round_trip() {
        let compiled = compile(FLOW, &offload(), REMOTE_CROSSING_CAPACITY).unwrap();

        assert_eq!(
            compiled.producers[0].ticket_timeout,
            engine_wait(FILTER_TIMEOUT)
                + engine_wait(DETECTOR_TIMEOUT)
                + REMOTE_RTT_BOUND
                + engine_wait(TRACKER_TIMEOUT)
        );
    }

    #[test]
    fn a_producers_edges_to_each_server_form_a_crossing() {
        let mut spec = offload();
        spec.edges
            .push(edge("camera", "detector", DETECTOR_TIMEOUT));

        let compiled = compile(FLOW, &spec, REMOTE_CROSSING_CAPACITY).unwrap();

        let from_camera: Vec<Location> = compiled
            .crossings
            .iter()
            .filter(|crossing| matches!(crossing.source, EdgeSource::Producer(_)))
            .map(|crossing| crossing.location)
            .collect();
        assert_eq!(from_camera, vec![Location::Local, Location::Remote]);
    }

    #[test]
    fn rejects_an_edge_from_a_remote_to_a_local_engine() {
        let mut spec = offload();
        spec.engines.push(engine("display"));
        spec.edges.push(edge("tracker", "display", FILTER_TIMEOUT));

        assert!(rejects(&spec));
    }

    #[test]
    fn token_count_defaults_to_the_places_on_the_paths() {
        let mut chain = cascade();
        chain.producers[0].tokens = None;
        let mut fan_out = chain.clone();
        fan_out
            .edges
            .push(edge("camera", "detector", DETECTOR_TIMEOUT));
        let mut remote = offload();
        remote.producers[0].tokens = None;

        let tokens = |spec: &FlowSpec| {
            compile(FLOW, spec, REMOTE_CROSSING_CAPACITY)
                .unwrap()
                .producers[0]
                .tokens
        };

        // 2 engines, 2 edges, and a spare.
        assert_eq!(tokens(&chain), 5);
        // The detector counts once, but each of its edges has a pending slot.
        assert_eq!(tokens(&fan_out), 6);
        // 3 engines, 3 edges, the uplink's capacity, and a spare.
        assert_eq!(tokens(&remote), 3 + 3 + REMOTE_CROSSING_CAPACITY + 1);
    }

    #[test]
    fn token_count_covers_only_the_producers_own_paths() {
        let mut spec = cascade();
        spec.producers[0].tokens = None;
        spec.producers.push(ProducerSpec {
            name: String::from("lidar"),
            tokens: None,
        });
        spec.edges.push(edge("lidar", "detector", DETECTOR_TIMEOUT));

        let compiled = compile(FLOW, &spec, REMOTE_CROSSING_CAPACITY).unwrap();

        let tokens: Vec<usize> = compiled.producers.iter().map(|p| p.tokens).collect();
        // The lidar reaches only the detector: 1 engine, 1 edge, and a spare.
        assert_eq!(tokens, vec![5, 3]);
    }

    #[test]
    fn a_given_token_count_is_kept_even_when_low() {
        let compiled = compile(FLOW, &cascade(), REMOTE_CROSSING_CAPACITY).unwrap();

        assert_eq!(compiled.producers[0].tokens, TOKENS);
    }
}
