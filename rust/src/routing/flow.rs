use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

use crate::routing::{
    edge::{EdgeAction, EdgeState},
    ids::{EdgeId, InputId, NodeId, OfferId},
    tokens::{OfferEnd, TokenPool, Verdict},
};

use crate::{Error, Result};

/// Where a client-owned edge starts.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) enum EdgeSource {
    Producer(String),
    Engine(NodeId),
}

/// A producer of the flow: its tokens and tickets, and its newest input not yet admitted.
#[derive(Debug)]
struct ProducerState<T> {
    pool: TokenPool<T>,
    waiting: Option<(InputId, T)>,
}

/// An edge the client owns. It tracks inputs by id; their data is in the producer's tickets.
#[derive(Debug)]
struct ClientEdge {
    state: EdgeState<InputId>,
    target: NodeId,
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
}

/// Client-side routing of one flow.
#[derive(Debug)]
pub(crate) struct FlowRouter<T> {
    session: u64,
    producers: HashMap<String, ProducerState<T>>,
    edges: HashMap<EdgeId, ClientEdge>,
    /// The client's edges leaving each producer and engine.
    outgoing: HashMap<EdgeSource, Vec<EdgeId>>,
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
        if self.producers.contains_key(name) {
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
        self.producers.insert(String::from(name), producer);
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

    /// A producer's newest input. Replaces the input waiting, if any, and tries to admit it.
    pub(crate) fn input_ready(
        &mut self,
        producer_name: &str,
        id: InputId,
        input: T,
        now: Instant,
    ) -> Result<()> {
        let Some(producer) = self.producers.get_mut(producer_name) else {
            return Err(Error::InvalidArgument(format!(
                "producer {producer_name:?} does not exist"
            )));
        };
        producer.waiting = Some((id, input));
        self.try_admit(producer_name, now);
        Ok(())
    }

    /// Admits the producer's waiting input if the producer has a token and one of its edges can
    /// send right away, then offers it to each of the producer's edges.
    fn try_admit(&mut self, producer_name: &str, now: Instant) {
        let edges = self
            .outgoing
            .get(&EdgeSource::Producer(producer_name.to_string()))
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
        let admitted = producer.pool.admit(id.clone(), input, now);
        debug_assert!(admitted, "a producer's input ids are unique");

        let offers: Vec<OfferId> = edges
            .iter()
            .map(|edge| {
                self.edges
                    .get_mut(edge)
                    .expect("edge was added")
                    .state
                    .offer(id.clone(), now)
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
    /// skipped or timed-out offers end in their input's pool.
    fn drain_edge_actions(&mut self, now: Instant) {
        for (&edge_id, edge) in self.edges.iter_mut() {
            while let Some(action) = edge.state.poll_action() {
                match action {
                    // The client's edges carry input ids; the inputs are in their tickets.
                    EdgeAction::Send { offer, input: id } => {
                        let input = self.producers[&id.producer]
                            .pool
                            .input(&id)
                            .expect("an input being sent has an open ticket")
                            .clone();
                        self.actions.push_back(RouterAction::Send {
                            edge: edge_id,
                            target: edge.target,
                            offer,
                            input_id: id,
                            input,
                        });
                    }
                    EdgeAction::Skipped { offer, input: id } => {
                        let producer = self
                            .producers
                            .get_mut(&id.producer)
                            .expect("producer was added");
                        producer.pool.end_offer(&id, offer, OfferEnd::Skipped, now);
                    }
                    EdgeAction::TimedOut { offer, input: id } => {
                        let producer = self
                            .producers
                            .get_mut(&id.producer)
                            .expect("producer was added");
                        producer.pool.end_offer(&id, offer, OfferEnd::TimedOut, now);
                    }
                }
            }
        }
    }

    /// Handles the end of one offer of the input `id`, as reported by an engine, a server or an
    /// edge.
    ///
    /// An event for an offer that is no longer open is not applied: it is `Late` if the offer
    /// timed out recently, and `Dropped` otherwise (a duplicate, or an unknown input).
    ///
    /// Otherwise the offer's edge is freed, and if the engine passed the input on, the input is
    /// offered on the client's edges leaving that engine, unless the ticket is past its deadline.
    /// Then the ticket is updated, and inputs that were waiting for a token or an edge are
    /// admitted. The returned verdict says whether the event was applied and whether the token
    /// came back.
    pub(crate) fn end_offer(
        &mut self,
        id: &InputId,
        offer: OfferId,
        end: OfferEnd,
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
        // Inform the edge that this offer has ended.
        // TODO: for a crossing, `Accepted` only means the server received the input. Its capacity
        // should be freed when the first of its targets finishes (design doc, "Crossings"), not
        // here; otherwise the client sends the next input while the remote engines are still busy.
        if let Some(edge) = self.edges.get_mut(&offer.edge) {
            edge.state.end(offer, now);
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
                            .offer(id.clone(), now);
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
        let names: Vec<String> = self.producers.keys().cloned().collect();
        for name in names {
            self.try_admit(&name, now);
        }
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

    /// The id the camera gives its `seq`th frame.
    fn input_id(seq: u64) -> InputId {
        InputId {
            producer: String::from(PRODUCER),
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
                    EdgeSource::Producer(String::from(PRODUCER)),
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
            .map(
                |RouterAction::Send {
                     edge,
                     input_id,
                     input,
                     ..
                 }| (edge.0, input_id.seq, input),
            )
            .collect();
        sends.sort();
        sends
    }

    /// The sends queued so far, as `(edge, offer)`, sorted by edge. Tests feed the offers back
    /// into `end_offer`, as the client does when the engines answer.
    fn sent_offers(router: &mut FlowRouter<u32>) -> Vec<(EdgeId, OfferId)> {
        let mut offers: Vec<_> = std::iter::from_fn(|| router.poll_action())
            .map(|RouterAction::Send { edge, offer, .. }| (edge, offer))
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

    #[test]
    fn input_is_admitted_and_sent_to_every_producer_edge() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE), (SLOW, SLOW_ENGINE, ONE)]);

        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10), (SLOW.0, 1, 10)]);
    }

    #[test]
    fn input_waits_while_no_token() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, TWO)]);

        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        router.input_ready(PRODUCER, input_id(2), 20, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10)]);
    }

    #[test]
    fn newer_input_replaces_the_waiting_one() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, TWO)]);
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        router.input_ready(PRODUCER, input_id(2), 20, now).unwrap();

        router.input_ready(PRODUCER, input_id(3), 30, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10)]);
        let waiting = &router.producers[PRODUCER].waiting;
        assert_eq!(waiting, &Some((input_id(3), 30)));
    }

    #[test]
    fn input_waits_while_every_edge_is_full() {
        let now = Instant::now();
        let mut router = router(TWO, &[(FAST, FAST_ENGINE, ONE)]);

        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        router.input_ready(PRODUCER, input_id(2), 20, now).unwrap();

        assert_eq!(sends(&mut router), vec![(FAST.0, 1, 10)]);
        assert!(router.producers[PRODUCER].pool.has_tokens());
    }

    #[test]
    fn input_for_an_unknown_producer_is_rejected() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);

        let result = router.input_ready("unknown", input_id(1), 10, now);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn early_discard_returns_the_token_at_the_filter() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE)]);
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        router.input_ready(PRODUCER, input_id(2), 20, now).unwrap();
        let [(_, offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(&input_id(1), offer, stopped(FAST_ENGINE), now);

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
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), now);

        assert_eq!(
            verdict,
            Verdict::Applied {
                token_returned: false
            }
        );
        assert!(!router.producers[PRODUCER].pool.has_tokens());
        assert_eq!(sends(&mut router), vec![(UPLINK.0, 1, 10)]);
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
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), now);
        let [(_, uplink_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(&input_id(1), uplink_offer, stopped(SLOW_ENGINE), now);

        assert_eq!(
            verdict,
            Verdict::Applied {
                token_returned: true
            }
        );
        assert!(router.producers[PRODUCER].pool.has_tokens());
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
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), now);
        assert_eq!(sends(&mut router), vec![(UPLINK.0, 1, 10)]);

        let verdict = router.end_offer(&input_id(1), filter_offer, passed(FAST_ENGINE), now);

        assert_eq!(verdict, Verdict::Dropped);
        assert_eq!(sends(&mut router), vec![]);
    }

    #[test]
    fn slow_branch_only_processes_inputs_the_fast_branch_got() {
        let now = Instant::now();
        let mut router = router(ONE, &[(FAST, FAST_ENGINE, ONE), (SLOW, SLOW_ENGINE, ONE)]);
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        router.input_ready(PRODUCER, input_id(2), 20, now).unwrap();
        let [(_, fast_offer), (_, slow_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send per edge");
        };

        // The fast branch finishes first: the token returns and input 2 is admitted. Only the
        // fast edge has room, so input 2 waits in the slow edge's pending slot.
        let fast = router.end_offer(&input_id(1), fast_offer, stopped(FAST_ENGINE), now);
        assert_eq!(
            fast,
            Verdict::Applied {
                token_returned: true
            }
        );
        assert_eq!(sends(&mut router), vec![(FAST.0, 2, 20)]);

        // The slow branch finishes input 1 late. The token already came back, so it is not
        // returned again, and the slow engine moves on to input 2.
        let slow = router.end_offer(&input_id(1), slow_offer, stopped(SLOW_ENGINE), now);
        assert_eq!(
            slow,
            Verdict::Applied {
                token_returned: false
            }
        );
        assert!(!router.producers[PRODUCER].pool.has_tokens());
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
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };

        let verdict = router.end_offer(
            &input_id(1),
            filter_offer,
            passed(FAST_ENGINE),
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
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        router.input_ready(PRODUCER, input_id(2), 20, now).unwrap();
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
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        let [(_, filter_offer)] = sent_offers(&mut router)[..] else {
            panic!("expected one send");
        };
        router.expire(now + TIMEOUT);

        let verdict = router.end_offer(
            &input_id(1),
            filter_offer,
            passed(FAST_ENGINE),
            now + TIMEOUT,
        );

        assert_eq!(verdict, Verdict::Late);
        assert_eq!(sends(&mut router), vec![]);
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
                EdgeSource::Producer(String::from(PRODUCER)),
                FAST_ENGINE,
                TWO,
                TIMEOUT,
            )
            .unwrap();
        router.input_ready(PRODUCER, input_id(1), 10, now).unwrap();
        router.input_ready(PRODUCER, input_id(2), 20, now).unwrap();
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
