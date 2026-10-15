use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

use crate::routing::{
    edge::{EdgeAction, EdgeState},
    ids::{EdgeId, InputId, NodeId, OfferId, ProducerName},
    tokens::{OfferEnd, TokenPool, Verdict},
};

use crate::{Error, Result};

/// Where a client-owned edge starts.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) enum EdgeSource {
    Producer(ProducerName),
    Engine(NodeId),
}

/// A producer of the flow: its tokens and tickets, and its newest input not yet admitted.
#[derive(Debug)]
struct ProducerState<T> {
    pool: TokenPool<T>,
    waiting: Option<(InputId, T)>,
}

/// An edge the client owns. It tracks inputs by id; their data is in the producer's tickets,
/// unless an engine emitted a new input.
#[derive(Debug)]
struct ClientEdge<T> {
    state: EdgeState<EdgeInput<T>>,
    target: NodeId,
}

/// An input on a client edge.
#[derive(Debug, Clone)]
struct EdgeInput<T> {
    id: InputId,
    /// The input to send, when it is not the one its ticket holds: one an engine emitted.
    data: Option<T>,
}

/// What the client must do after calling into the router.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum RouterAction<T> {
    /// Send `input`, whose id is `input_id`, along `edge` to the engine `target`.
    Send {
        edge: EdgeId,
        target: NodeId,
        offer: OfferId,
        input_id: InputId,
        input: T,
    },
    /// Report that `offer`, of the input `input_id`, was replaced in the pending slot of `edge`
    /// and never sent.
    Skipped {
        edge: EdgeId,
        offer: OfferId,
        input_id: InputId,
    },
    /// Report that `offer`, of the input `input_id`, was sent along `edge` but not answered
    /// before its deadline.
    TimedOut {
        edge: EdgeId,
        offer: OfferId,
        input_id: InputId,
    },
}

/// Client-side routing of one flow.
#[derive(Debug)]
pub(crate) struct FlowRouter<T> {
    session: u64,
    producers: HashMap<ProducerName, ProducerState<T>>,
    edges: HashMap<EdgeId, ClientEdge<T>>,
    /// The client's edges leaving each producer and engine.
    outgoing: HashMap<EdgeSource, Vec<EdgeId>>,
    /// The crossing offer each server offer was created from, while that crossing still holds
    /// capacity on its edge.
    crossing_of: HashMap<OfferId, OfferId>,
    actions: VecDeque<RouterAction<T>>,
}

impl<T: Clone> FlowRouter<T> {
    /// Creates a router for one flow. `session` makes the client's offer ids unique across
    /// restarts.
    pub(crate) fn new(session: u64) -> Self {
        Self {
            session,
            producers: HashMap::new(),
            edges: HashMap::new(),
            outgoing: HashMap::new(),
            crossing_of: HashMap::new(),
            actions: VecDeque::new(),
        }
    }

    /// Adds a producer with `tokens` tokens whose tickets return their tokens after
    /// `ticket_timeout` at the latest.
    pub(crate) fn add_producer(
        &mut self,
        name: &str,
        tokens: usize,
        ticket_timeout: Duration,
    ) -> Result<()> {
        if name.is_empty() {
            return Err(Error::InvalidArgument(String::from(
                "producer name is empty",
            )));
        }
        let name = ProducerName::try_from(name)
            .map_err(|_| Error::InvalidArgument(format!("producer name {name:?} is too long")))?;
        if self.producers.contains_key(&name) {
            return Err(Error::InvalidArgument(format!(
                "producer {name:?} already exists"
            )));
        }
        if tokens == 0 {
            return Err(Error::InvalidArgument(format!(
                "producer {name:?} needs at least 1 token",
            )));
        }
        let producer = ProducerState {
            pool: TokenPool::new(tokens, ticket_timeout),
            waiting: None,
        };
        self.producers.insert(name, producer);
        Ok(())
    }

    /// Adds a client-owned edge from `source` to the engine `target`, which may have `capacity`
    /// inputs in flight, each for at most `timeout`.
    pub(crate) fn add_edge(
        &mut self,
        edge_id: EdgeId,
        source: EdgeSource,
        target: NodeId,
        capacity: usize,
        timeout: Duration,
    ) -> Result<()> {
        if self.edges.contains_key(&edge_id) {
            return Err(Error::InvalidArgument(format!(
                "edge {edge_id:?} already exists"
            )));
        }
        if capacity == 0 {
            return Err(Error::InvalidArgument(format!(
                "edge {edge_id:?} needs a capacity of at least 1"
            )));
        }
        if let EdgeSource::Producer(name) = &source
            && !self.producers.contains_key(name)
        {
            return Err(Error::InvalidArgument(format!(
                "producer {name:?} does not exist"
            )));
        }
        let state = EdgeState::new(edge_id, self.session, capacity, timeout);
        let edge = ClientEdge { state, target };
        self.edges.insert(edge_id, edge);
        self.outgoing.entry(source).or_default().push(edge_id);
        Ok(())
    }

    /// Returns the next action for the client to carry out.
    pub(crate) fn poll_action(&mut self) -> Option<RouterAction<T>> {
        self.actions.pop_front()
    }

    /// The newest input of the producer `id.producer`. Replaces the input waiting, if any, and
    /// tries to admit it.
    pub(crate) fn input_ready(&mut self, id: InputId, input: T, now: Instant) -> Result<()> {
        let producer_name = id.producer;
        let Some(producer) = self.producers.get_mut(&producer_name) else {
            return Err(Error::InvalidArgument(format!(
                "producer {producer_name:?} does not exist"
            )));
        };
        producer.waiting = Some((id, input));
        self.try_admit(&producer_name, now);
        Ok(())
    }

    /// Admits the producer's waiting input if the producer has a token and one of its edges can
    /// send right away, then offers it to each of the producer's edges.
    fn try_admit(&mut self, producer_name: &ProducerName, now: Instant) {
        let edges = self
            .outgoing
            .get(&EdgeSource::Producer(*producer_name))
            .cloned()
            .unwrap_or_default();
        let can_send = edges
            .iter()
            .any(|edge| self.edges[edge].state.has_spare_capacity());
        let producer = self
            .producers
            .get_mut(producer_name)
            .expect("producer was added");
        if !can_send || !producer.pool.has_tokens() {
            return;
        }
        let Some((id, input)) = producer.waiting.take() else {
            return;
        };
        let admitted = producer.pool.admit(id, input, now);
        debug_assert!(admitted, "a producer's input ids are unique");

        let offers: Vec<OfferId> = edges
            .iter()
            .map(|edge| {
                self.edges
                    .get_mut(edge)
                    .expect("edge was added")
                    .state
                    .offer(EdgeInput { id, data: None }, now)
            })
            .collect();
        let producer = self
            .producers
            .get_mut(producer_name)
            .expect("producer was added");
        producer
            .pool
            .add_offers(&id, offers)
            .expect("the input was just admitted");

        self.drain_edge_actions(now);
    }

    /// Turns the edges' queued actions into the router's: sends become actions for the client, and
    /// skipped or timed-out offers end in their input's pool, and are reported if that ended them.
    /// A crossing that times out after `Accepted` has already ended in the pool, so its timeout
    /// only frees the edge, and is not reported.
    fn drain_edge_actions(&mut self, now: Instant) {
        for (&edge_id, edge) in self.edges.iter_mut() {
            while let Some(action) = edge.state.poll_action() {
                match action {
                    // The client's edges carry input ids; the inputs are in their tickets, unless
                    // an engine emitted them.
                    EdgeAction::Send {
                        offer,
                        input: EdgeInput { id, data },
                    } => {
                        let input = data.unwrap_or_else(|| {
                            self.producers[&id.producer]
                                .pool
                                .input(&id)
                                .expect("an input being sent has an open ticket")
                                .clone()
                        });
                        self.actions.push_back(RouterAction::Send {
                            edge: edge_id,
                            target: edge.target,
                            offer,
                            input_id: id,
                            input,
                        });
                    }
                    EdgeAction::Skipped {
                        offer,
                        input: EdgeInput { id, .. },
                    } => {
                        let producer = self
                            .producers
                            .get_mut(&id.producer)
                            .expect("producer was added");
                        let verdict = producer.pool.end_offer(&id, offer, OfferEnd::Skipped, now);
                        if matches!(verdict, Verdict::Applied { .. }) {
                            self.actions.push_back(RouterAction::Skipped {
                                edge: edge_id,
                                offer,
                                input_id: id,
                            });
                        }
                    }
                    EdgeAction::TimedOut {
                        offer,
                        input: EdgeInput { id, .. },
                    } => {
                        // A crossing that times out after `Accepted` no longer holds capacity, so
                        // its children's ends must not free it again. Its offer already ended in
                        // the pool, which drops this event.
                        self.crossing_of.retain(|_, crossing| *crossing != offer);
                        let producer = self
                            .producers
                            .get_mut(&id.producer)
                            .expect("producer was added");
                        let verdict = producer.pool.end_offer(&id, offer, OfferEnd::TimedOut, now);
                        if matches!(verdict, Verdict::Applied { .. }) {
                            self.actions.push_back(RouterAction::TimedOut {
                                edge: edge_id,
                                offer,
                                input_id: id,
                            });
                        }
                    }
                }
            }
        }
    }

    /// Whether `offer` of the input `id` has not ended yet.
    pub(crate) fn is_offer_open(&self, id: &InputId, offer: OfferId) -> bool {
        self.producers
            .get(&id.producer)
            .is_some_and(|producer| producer.pool.is_offer_open(id, offer))
    }

    /// Handles the end of one offer of the input `id`, as reported by an engine, a server or an
    /// edge.
    ///
    /// An event for an offer that is no longer open is not applied: it is `Late` if the offer
    /// timed out recently, and `Dropped` otherwise (a duplicate, or an unknown input).
    ///
    /// Otherwise the offer's edge is freed, and if the engine passed the input on, the input is
    /// offered on the client's edges leaving that engine, unless the ticket is past its deadline.
    /// What is offered is `emitted`, if the engine forwarded an input the ticket does not hold
    /// (one it, or an engine before it, emitted), and the ticket's input otherwise.
    /// A crossing is the exception: `Accepted` only means the server received the input, so the
    /// crossing keeps its capacity until the first of its children ends, or until it times out.
    /// Then the ticket is updated, and inputs that were waiting for a token or an edge are
    /// admitted. The returned verdict says whether the event was applied and whether the token
    /// came back.
    pub(crate) fn end_offer(
        &mut self,
        id: &InputId,
        offer: OfferId,
        end: OfferEnd,
        emitted: Option<T>,
        now: Instant,
    ) -> Verdict {
        let producer_name = &id.producer;
        let Some(producer) = self.producers.get_mut(producer_name) else {
            return Verdict::Dropped;
        };
        if !producer.pool.is_offer_open(id, offer) {
            // The offer already ended: a late result after a timeout, or a duplicate event.
            return producer.pool.classify_stale(offer, &end);
        }
        match &end {
            // The crossing holds its capacity until one of its children ends, so the client sends
            // the next input only when a target engine is done with this one. A crossing with no
            // children has nothing to wait for, and is freed below.
            OfferEnd::Accepted { children } if !children.is_empty() => {
                for &child in children {
                    self.crossing_of.insert(child, offer);
                }
            }
            _ => {
                // The first child of a crossing to end frees the crossing, and its siblings no
                // longer refer to it.
                let freed = match self.crossing_of.remove(&offer) {
                    Some(crossing) => {
                        self.crossing_of.retain(|_, other| *other != crossing);
                        crossing
                    }
                    None => offer,
                };
                if let Some(edge) = self.edges.get_mut(&freed.edge) {
                    edge.state.end(freed, now);
                }
            }
        }
        let end = match end {
            OfferEnd::Result {
                node,
                forwarded,
                mut children,
            } => {
                let past_deadline = producer.pool.is_past_deadline(id, now);
                if forwarded && !past_deadline {
                    let next_edges = self
                        .outgoing
                        .get(&EdgeSource::Engine(node))
                        .cloned()
                        .unwrap_or_default();
                    for edge in next_edges {
                        let new_offer = self
                            .edges
                            .get_mut(&edge)
                            .expect("edge was added")
                            .state
                            .offer(
                                EdgeInput {
                                    id: *id,
                                    data: emitted.clone(),
                                },
                                now,
                            );
                        children.push(new_offer);
                    }
                }
                OfferEnd::Result {
                    node,
                    forwarded,
                    children,
                }
            }
            other => other,
        };
        let producer = self
            .producers
            .get_mut(&id.producer)
            .expect("producer exists, checked above");
        let verdict = producer.pool.end_offer(id, offer, end, now);
        self.drain_edge_actions(now);
        self.try_admit_all(now);
        verdict
    }

    /// Tries to admit the waiting input of every producer. Called whenever a token or an edge slot
    /// may have been freed.
    fn try_admit_all(&mut self, now: Instant) {
        let names: Vec<ProducerName> = self.producers.keys().copied().collect();
        for name in &names {
            self.try_admit(name, now);
        }
    }

    /// The client lost its connection to a server: the one whose offers carry the session
    /// `server_session`, and that the client's edges `crossings` lead to.
    ///
    /// The crossings stop sending until [`FlowRouter::reconnect`], and their pending offers are
    /// skipped, so nothing old is sent once the server is back. Every offer on that server then ends
    /// with `Disconnected`: the offers the server made, and the crossing offers sent to it, whose
    /// events may have been lost with the connection. Inputs whose only paths lead to that server
    /// are not admitted until then.
    pub(crate) fn disconnect(&mut self, server_session: u64, crossings: &[EdgeId], now: Instant) {
        for crossing in crossings {
            if let Some(edge) = self.edges.get_mut(crossing) {
                edge.state.suspend();
                edge.state.skip_pending();
            }
        }
        // The sends not carried out yet are for offers that end below.
        self.actions.retain(
            |action| !matches!(action, RouterAction::Send { edge, .. } if crossings.contains(edge)),
        );
        self.drain_edge_actions(now);

        let session = self.session;
        let on_server = |offer: &OfferId| {
            offer.session == server_session
                || (offer.session == session && crossings.contains(&offer.edge))
        };
        let lost: Vec<(InputId, OfferId)> = self
            .producers
            .values()
            .flat_map(|producer| producer.pool.open_offers())
            .filter(|(_, offer)| on_server(offer))
            .collect();
        for (id, offer) in lost {
            self.end_offer(&id, offer, OfferEnd::Disconnected, None, now);
        }
    }

    /// The crossings `crossings` send nothing until [`FlowRouter::reconnect`], e.g. because their
    /// server's part of the flow is not installed yet.
    pub(crate) fn suspend(&mut self, crossings: &[EdgeId]) {
        for crossing in crossings {
            if let Some(edge) = self.edges.get_mut(crossing) {
                edge.state.suspend();
            }
        }
    }

    /// The client is connected to the server again: the crossings `crossings` send again, and
    /// waiting inputs are admitted.
    pub(crate) fn reconnect(&mut self, crossings: &[EdgeId], now: Instant) {
        for crossing in crossings {
            if let Some(edge) = self.edges.get_mut(crossing) {
                edge.state.resume(now);
            }
        }
        self.drain_edge_actions(now);
        self.try_admit_all(now);
    }

    /// Handles the passing of time: ends the offers whose edge deadline passed, returns the tokens
    /// of tickets past their deadline, and admits waiting inputs into the freed capacity. The
    /// client calls this periodically.
    pub(crate) fn expire(&mut self, now: Instant) {
        for edge in self.edges.values_mut() {
            edge.state.expire(now);
        }
        // Timed-out offers end in their pools, which can return tokens.
        self.drain_edge_actions(now);
        for producer in self.producers.values_mut() {
            producer.pool.expire(now);
        }
        self.try_admit_all(now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SESSION: u64 = 1;
    const PRODUCER: &str = "camera";
    const PRODUCER_SESSION: u64 = 7;
    const TIMEOUT: Duration = Duration::from_secs(1);
    const ONE: usize = 1;
    const TWO: usize = 2;
    const FAST: EdgeId = EdgeId(1);
    const SLOW: EdgeId = EdgeId(2);
    const FAST_ENGINE: NodeId = NodeId(1);
    const SLOW_ENGINE: NodeId = NodeId(2);
    const UPLINK: EdgeId = EdgeId(3);
    /// Shorter than `TIMEOUT`, so a ticket's deadline comes before its edges' timeouts.
    const SHORT_TICKET_TIMEOUT: Duration = Duration::from_millis(500);
    /// The session of the server that receives the crossing in these tests.
    const SERVER_SESSION: u64 = 9;
    /// The server's edges to the crossing's two target engines.
    const FIRST_TARGET: EdgeId = EdgeId(10);
    const SECOND_TARGET: EdgeId = EdgeId(11);

    fn producer() -> ProducerName {
        ProducerName::try_from(PRODUCER).unwrap()
    }

    /// The id the camera gives its `seq`th frame.
    fn input_id(seq: u64) -> InputId {
        InputId {
            producer: producer(),
            session: PRODUCER_SESSION,
            seq,
        }
    }

    /// A flow with one producer that has `tokens` tokens and one edge per `(edge, engine,
    /// capacity)`.
    fn router(tokens: usize, edges: &[(EdgeId, NodeId, usize)]) -> FlowRouter<u32> {
        let mut router = FlowRouter::new(SESSION);
        router.add_producer(PRODUCER, tokens, TIMEOUT).unwrap();
        for &(edge, engine, capacity) in edges {
            router
                .add_edge(
                    edge,
                    EdgeSource::Producer(producer()),
                    engine,
                    capacity,
                    TIMEOUT,
                )
                .unwrap();
        }
        router
    }

    /// The sends queued so far, as `(edge, input seq, input)`, sorted because the router's edges
    /// are in no particular order.
    fn sends(router: &mut FlowRouter<u32>) -> Vec<(u32, u64, u32)> {
        let mut sends: Vec<_> = std::iter::from_fn(|| router.poll_action())
            .filter_map(|action| match action {
                RouterAction::Send {
                    edge,
                    input_id,
                    input,
                    ..
                } => Some((edge.0, input_id.seq, input)),
                _ => None,
            })
            .collect();
        sends.sort();
        sends
    }

    /// The sends queued so far, as `(edge, offer)`, sorted by edge. Tests feed the offers back
    /// into `end_offer`, as the client does when the engines answer.
    fn sent_offers(router: &mut FlowRouter<u32>) -> Vec<(EdgeId, OfferId)> {
        let mut offers: Vec<_> = std::iter::from_fn(|| router.poll_action())
            .filter_map(|action| match action {
                RouterAction::Send { edge, offer, .. } => Some((edge, offer)),
                _ => None,
            })
            .collect();
        offers.sort_by_key(|(edge, _)| edge.0);
        offers
    }

    /// The engine `node` finished the input and forwarded nothing.
    fn stopped(node: NodeId) -> OfferEnd {
        OfferEnd::Result {
            node,
            forwarded: false,
            children: Vec::new(),
        }
    }

    /// The engine `node` passed the input on, creating no offers of its own.
    fn passed(node: NodeId) -> OfferEnd {
        OfferEnd::Result {
            node,
            forwarded: true,
            children: Vec::new(),
        }
    }

    /// The offer the server created for the `seq`th input it received on the target edge `edge`.
    fn server_offer(edge: EdgeId, seq: u64) -> OfferId {
        OfferId {
            session: SERVER_SESSION,
            edge,
            seq,
        }
    }

    /// The server received the crossing offer of the `seq`th input and offered it to both targets.
    fn accepted(seq: u64) -> OfferEnd {
        OfferEnd::Accepted {
            children: vec![
                server_offer(FIRST_TARGET, seq),
                server_offer(SECOND_TARGET, seq),
            ],
        }
    }

    #[test]
    fn crossing_keeps_its_capacity_until_its_first_child_ends() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, crossing)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        // The server has the input, but neither target has finished it: input 2 waits.
        router.end_offer(&input_id(1), crossing, accepted(1), None, now);
        router.input_ready(input_id(2), 20, now).unwrap();
        assert_eq!(sends(&mut router), vec![]);

        // The first target finishes: the crossing is free, and input 2 is sent.
        let first = router.end_offer(
            &input_id(1),
            server_offer(FIRST_TARGET, 1),
            stopped(FAST_ENGINE),
            None,
            now,
        );
        assert_eq!(
            first,
            Verdict::Applied {
                token_returned: true
            }
        );
        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);

        // The second target finishes too. Input 2's crossing still holds the capacity, so input
        // 3 waits.
        router.end_offer(
            &input_id(1),
            server_offer(SECOND_TARGET, 1),
            stopped(SLOW_ENGINE),
            None,
            now,
        );
        router.input_ready(input_id(3), 30, now).unwrap();
        assert_eq!(sends(&mut router), vec![]);
    }

    #[test]
    fn crossing_timeout_after_accepted_frees_its_capacity_once() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, crossing)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.end_offer(&input_id(1), crossing, accepted(1), None, now);
        router.input_ready(input_id(2), 20, now).unwrap();

        // No target finishes in time: the crossing times out, and input 2 is sent.
        router.expire(now + TIMEOUT);
        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);

        // A target finishes input 1 afterwards. Its offer is still open, so the event is applied,
        // but it must not free the capacity that input 2's crossing holds.
        let late = router.end_offer(
            &input_id(1),
            server_offer(FIRST_TARGET, 1),
            stopped(FAST_ENGINE),
            None,
            now + TIMEOUT,
        );
        assert_eq!(
            late,
            Verdict::Applied {
                token_returned: false
            }
        );
        router.input_ready(input_id(3), 30, now + TIMEOUT).unwrap();
        assert_eq!(sends(&mut router), vec![]);
    }

    #[test]
    fn crossing_accepted_with_no_children_is_freed_right_away() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();
        let [(_, crossing)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let no_children = OfferEnd::Accepted {
            children: Vec::new(),
        };
        router.end_offer(&input_id(1), crossing, no_children, None, now);

        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);
    }

    #[test]
    fn input_is_admitted_and_sent_to_every_producer_edge() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE), (SLOW, SLOW_ENGINE, ONE)]);

        router.input_ready(input_id(1), 10, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10), (SLOW.0, 1, 10)]);
    }

    #[test]
    fn input_waits_while_no_token() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, TWO)]);

        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10)]);
    }

    #[test]
    fn newer_input_replaces_the_waiting_one() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, TWO)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();

        router.input_ready(input_id(3), 30, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10)]);
        let waiting = &router.producers[&producer()].waiting;
        assert_eq!(waiting, &Some((input_id(3), 30)));
    }

    #[test]
    fn input_waits_while_every_edge_is_full() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);

        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10)]);
        assert!(router.producers[&producer()].pool.has_tokens());
    }

    #[test]
    fn input_for_an_unknown_producer_is_rejected() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);

        let unknown_id = InputId {
            producer: ProducerName::try_from("unknown").unwrap(),
            ..input_id(1)
        };

        let result = router.input_ready(unknown_id, 10, now);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn early_discard_returns_the_token_at_the_filter() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();
        let [(_, offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(&input_id(1), offer, stopped(FAST_ENGINE), None, now);

        assert_eq!(
            verdict,
            Verdict::Applied {
                token_returned: true
            }
        );
        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);
    }

    #[test]
    fn pass_across_the_uplink_keeps_the_token_until_the_detector_finishes() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router
            .add_edge(
                UPLINK,
                EdgeSource::Engine(FAST_ENGINE),
                SLOW_ENGINE,
                ONE,
                TIMEOUT,
            )
            .unwrap();
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), None, now);

        assert_eq!(
            verdict,
            Verdict::Applied {
                token_returned: false
            }
        );
        assert!(!router.producers[&producer()].pool.has_tokens());
        assert_eq!(sends(&mut router), vec![(UPLINK.0, 1, 10)]);
    }

    /// A router whose producer feeds the filter, which feeds the detector over an uplink.
    fn router_with_uplink() -> FlowRouter<u32> {
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, TWO)]);
        router
            .add_edge(
                UPLINK,
                EdgeSource::Engine(FAST_ENGINE),
                SLOW_ENGINE,
                TWO,
                TIMEOUT,
            )
            .unwrap();
        router
    }

    #[test]
    fn emitted_input_crosses_the_uplink_in_place_of_the_original() {
        const TILES: u32 = 99;
        let now = Instant::now();
        let mut router = router_with_uplink();
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        router.end_offer(
            &input_id(1),
            filter_offer,
            passed(FAST_ENGINE),
            Some(TILES),
            now,
        );

        assert_eq!(sends(&mut router), vec![(UPLINK.0, 1, TILES)]);
    }

    #[test]
    fn an_emitted_input_does_not_replace_the_tickets_input() {
        const TILES: u32 = 99;
        let now = Instant::now();
        let mut router = router_with_uplink();
        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();
        let [(_, first), (_, second)] = sent_offers(&mut router)[..] else {
            panic!("expected two sends");
        };

        router.end_offer(&input_id(1), first, passed(FAST_ENGINE), Some(TILES), now);
        router.end_offer(&input_id(2), second, passed(FAST_ENGINE), None, now);

        assert_eq!(
            sends(&mut router),
            vec![(UPLINK.0, 1, TILES), (UPLINK.0, 2, 20)]
        );
    }

    #[test]
    fn detector_finishing_after_the_uplink_returns_the_token() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router
            .add_edge(
                UPLINK,
                EdgeSource::Engine(FAST_ENGINE),
                SLOW_ENGINE,
                ONE,
                TIMEOUT,
            )
            .unwrap();
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), None, now);
        let [(_, uplink_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(&input_id(1), uplink_offer, stopped(SLOW_ENGINE), None, now);

        assert_eq!(
            verdict,
            Verdict::Applied {
                token_returned: true
            }
        );
        assert!(router.producers[&producer()].pool.has_tokens());
    }

    #[test]
    fn duplicate_pass_is_not_forwarded_twice() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router
            .add_edge(
                UPLINK,
                EdgeSource::Engine(FAST_ENGINE),
                SLOW_ENGINE,
                TWO,
                TIMEOUT,
            )
            .unwrap();
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), None, now);
        assert_eq!(sends(&mut router), vec![(UPLINK.0, 1, 10)]);

        let verdict = router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), None, now);

        assert_eq!(verdict, Verdict::Dropped);
        assert_eq!(sends(&mut router), vec![]);
    }

    #[test]
    fn slow_branch_only_processes_inputs_the_fast_branch_got() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE), (SLOW, SLOW_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();
        let [(_, fast_offer), (_, slow_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send per edge");
        };

        // The fast branch finishes first: the token returns and input 2 is admitted. Only the
        // fast edge has room, so input 2 waits in the slow edge's pending slot.
        let fast = router.end_offer(&input_id(1), fast_offer, stopped(FAST_ENGINE), None, now);
        assert_eq!(
            fast,
            Verdict::Applied {
                token_returned: true
            }
        );
        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);

        // The slow branch finishes input 1 late. The token already came back, so it is not
        // returned again, and the slow engine moves on to input 2.
        let slow = router.end_offer(&input_id(1), slow_offer, stopped(SLOW_ENGINE), None, now);
        assert_eq!(
            slow,
            Verdict::Applied {
                token_returned: false
            }
        );
        assert!(!router.producers[&producer()].pool.has_tokens());
        assert_eq!(sends(&mut router), vec![(SLOW.0, 2, 20)]);
    }

    #[test]
    fn result_after_the_ticket_deadline_is_not_forwarded() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router
            .add_edge(
                UPLINK,
                EdgeSource::Engine(FAST_ENGINE),
                SLOW_ENGINE,
                ONE,
                TIMEOUT,
            )
            .unwrap();
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(
            &input_id(1),
            filter_offer,
            passed(FAST_ENGINE),
            None,
            now + TIMEOUT,
        );

        assert_eq!(
            verdict,
            Verdict::Applied {
                token_returned: true
            }
        );
        assert_eq!(sends(&mut router), vec![]);
    }

    #[test]
    fn edge_timeout_returns_the_token_and_admits_the_waiting_input() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();
        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10)]);

        router.expire(now + TIMEOUT);

        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);
    }

    #[test]
    fn late_result_after_a_timeout_is_marked_late_and_not_forwarded() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router
            .add_edge(
                UPLINK,
                EdgeSource::Engine(FAST_ENGINE),
                SLOW_ENGINE,
                ONE,
                TIMEOUT,
            )
            .unwrap();
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.expire(now + TIMEOUT);

        let verdict = router.end_offer(
            &input_id(1),
            filter_offer,
            passed(FAST_ENGINE),
            None,
            now + TIMEOUT,
        );

        assert_eq!(verdict, Verdict::Late);
        assert_eq!(sends(&mut router), vec![]);
    }

    /// Offers the router reported as `(edge, input seq)`: first the skipped, then the timed out.
    type Reported = (Vec<(u32, u64)>, Vec<(u32, u64)>);

    /// The skips and timeouts the router reported so far, sorted. Sends are dropped.
    fn reported(router: &mut FlowRouter<u32>) -> Reported {
        let (mut skipped, mut timed_out) = (Vec::new(), Vec::new());
        for action in std::iter::from_fn(|| router.poll_action()) {
            match action {
                RouterAction::Skipped { edge, input_id, .. } => {
                    skipped.push((edge.0, input_id.seq));
                }
                RouterAction::TimedOut { edge, input_id, .. } => {
                    timed_out.push((edge.0, input_id.seq));
                }
                RouterAction::Send { .. } => {}
            }
        }
        skipped.sort();
        timed_out.sort();
        (skipped, timed_out)
    }

    /// A router whose fast edge has finished input 1 and has input 2 in flight, while the slow
    /// edge still has input 1, and input 2 waits in its pending slot.
    fn slow_edge_with_a_pending_input(now: Instant) -> FlowRouter<u32> {
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE), (SLOW, SLOW_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, fast_offer), _] = sent_offers(&mut router)[..] else {
            panic!("expected one send per edge");
        };
        router.input_ready(input_id(2), 20, now).unwrap();
        router.end_offer(&input_id(1), fast_offer, stopped(FAST_ENGINE), None, now);
        router
    }

    #[test]
    fn skipped_and_timed_out_offers_are_reported() {
        let now = Instant::now();
        let mut router = slow_edge_with_a_pending_input(now);
        let [(_, fast_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected input 2 on the fast edge");
        };

        // The fast edge finishes input 2, so input 3 is admitted and replaces input 2 at the
        // slow edge.
        router.input_ready(input_id(3), 30, now).unwrap();
        router.end_offer(&input_id(2), fast_offer, stopped(FAST_ENGINE), None, now);
        assert_eq!(reported(&mut router), (vec![(SLOW.0, 2)], vec![]));

        router.expire(now + TIMEOUT);

        assert_eq!(
            reported(&mut router),
            (vec![], vec![(FAST.0, 3), (SLOW.0, 1)])
        );
    }

    #[test]
    fn results_after_a_crossing_timed_out_before_accepted_are_late() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, crossing)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.expire(now + TIMEOUT);

        let later = now + TIMEOUT;
        let verdict = router.end_offer(&input_id(1), crossing, accepted(1), None, later);
        assert_eq!(verdict, Verdict::Late);
        // The children ended with their crossing, so their `Sent` is not published.
        let child = server_offer(FIRST_TARGET, 1);
        assert!(!router.is_offer_open(&input_id(1), child));
        let late = router.end_offer(&input_id(1), child, stopped(FAST_ENGINE), None, later);
        assert_eq!(late, Verdict::Late);
    }

    #[test]
    fn crossing_timeout_after_accepted_is_not_reported() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, crossing)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.end_offer(&input_id(1), crossing, accepted(1), None, now);

        router.expire(now + TIMEOUT);

        assert_eq!(reported(&mut router), (vec![], vec![]));
    }

    #[test]
    fn disconnect_ends_the_crossing_and_admits_nothing_until_reconnect() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, crossing)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        router.disconnect(SERVER_SESSION, &[FAST], now);

        // The token came back, but the crossing cannot send, so input 2 waits.
        assert!(router.producers[&producer()].pool.has_tokens());
        router.input_ready(input_id(2), 20, now).unwrap();
        router.input_ready(input_id(3), 30, now).unwrap();
        assert_eq!(sends(&mut router), vec![]);
        // A result from before the disconnection is late.
        let late = router.end_offer(&input_id(1), crossing, stopped(FAST_ENGINE), None, now);
        assert_eq!(late, Verdict::Late);

        router.reconnect(&[FAST], now);
        assert_eq!(sends(&mut router), vec![(FAST.0, 3, 30)]);
    }

    #[test]
    fn disconnect_ends_the_servers_offers_and_frees_the_crossing() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(input_id(1), 10, now).unwrap();
        let [(_, crossing)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.end_offer(&input_id(1), crossing, accepted(1), None, now);

        router.disconnect(SERVER_SESSION, &[FAST], now);
        router.reconnect(&[FAST], now);

        // Both children ended, so the ticket is closed, and the crossing has room again.
        assert!(
            router.producers[&producer()]
                .pool
                .input(&input_id(1))
                .is_none()
        );
        router.input_ready(input_id(2), 20, now).unwrap();
        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);
    }

    #[test]
    fn disconnect_skips_the_crossings_pending_offer() {
        let now = Instant::now();
        let mut router = slow_edge_with_a_pending_input(now);
        sends(&mut router);

        router.disconnect(SERVER_SESSION, &[FAST, SLOW], now);
        router.reconnect(&[FAST, SLOW], now);

        // Input 2 was skipped at the slow edge rather than sent late, and both tokens are back.
        assert_eq!(reported(&mut router), (vec![(SLOW.0, 2)], vec![]));
        assert!(
            router.producers[&producer()]
                .pool
                .input(&input_id(2))
                .is_none()
        );
        router.input_ready(input_id(3), 30, now).unwrap();
        assert_eq!(sends(&mut router), vec![(FAST.0, 3, 30), (SLOW.0, 3, 30)]);
    }

    #[test]
    fn ticket_deadline_returns_the_token_but_keeps_the_ticket() {
        let now = Instant::now();
        let mut router = FlowRouter::new(SESSION);
        router
            .add_producer(PRODUCER, ONE, SHORT_TICKET_TIMEOUT)
            .unwrap();
        router
            .add_edge(
                FAST,
                EdgeSource::Producer(producer()),
                FAST_ENGINE,
                TWO,
                TIMEOUT,
            )
            .unwrap();
        router.input_ready(input_id(1), 10, now).unwrap();
        router.input_ready(input_id(2), 20, now).unwrap();
        let [(_, offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        // The deadline comes before the edge's timeout: the token returns and input 2 is
        // admitted, while input 1's offer is still on the edge.
        router.expire(now + SHORT_TICKET_TIMEOUT);
        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);

        // The engine then answers input 1. The token is not returned a second time.
        let verdict = router.end_offer(
            &input_id(1),
            offer,
            stopped(FAST_ENGINE),
            None,
            now + SHORT_TICKET_TIMEOUT,
        );
        assert_eq!(
            verdict,
            Verdict::Applied {
                token_returned: false
            }
        );
    }
}
