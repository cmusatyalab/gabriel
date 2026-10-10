use std::{
    collections::{HashMap, HashSet},
    time::{Duration, Instant},
};

use crate::Result;
use crate::{
    Error,
    routing::ids::{InputId, NodeId, OfferId},
};

/// How long the client remembers an offer that timed out, so that a late `Result` for it is told
/// apart from a duplicate.
const LATE_RESULT_WINDOW: Duration = Duration::from_secs(60);

/// How an offer ended, as reported to the client.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum OfferEnd {
    /// The engine `node` reported an outcome. `forwarded` says whether it passed the input on
    /// (`Pass` or `Emit`), so the client can forward it on its own edges. `children` are the offers
    /// this created: the server's, plus the client's own (e.g. an uplink after `Pass`). No children
    /// means the input completed on this path.
    Result {
        node: NodeId,
        forwarded: bool,
        children: Vec<OfferId>,
    },
    /// A server received a crossing and created one offer per target engine.
    Accepted { children: Vec<OfferId> },
    /// The offer was replaced in a pending slot and never sent.
    Skipped,
    /// The offer was sent, but its engine did not answer before its deadline, or was lost.
    TimedOut,
    /// The connection to the server holding the offer was lost.
    Disconnected,
}

/// What the client should do with an event after applying it to the pool.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Verdict {
    /// The event was applied; `token_returned` says whether the input's token came back.
    Applied { token_returned: bool },
    /// A `Result` for an offer that already timed out: deliver it marked late.
    Late,
    /// A duplicate or unknown event: drop it.
    Dropped,
}

/// The client's record of one admitted input.
#[derive(Debug)]
struct Ticket<T> {
    /// The input itself, kept until the input is done, because the client may have to send it on
    /// one of its own edges after any number of engines have passed it on.
    input: T,
    /// Offers of this input that have not ended yet.
    open: HashSet<OfferId>,
    /// Whether the input still holds its token.
    token_held: bool,
    /// When the token is returned at the latest, even if offers are still open.
    deadline: Instant,
}

/// The tokens and tickets of one producer in one flow. Each admitted input takes a token, which
/// comes back when the input finishes, not over time as in a rate-limiting token bucket.
#[derive(Debug)]
pub(crate) struct TokenPool<T> {
    capacity: usize,
    available: usize,
    ticket_timeout: Duration,
    tickets: HashMap<InputId, Ticket<T>>,
    /// Offers that ended with TimedOut or Disconnected, and when.
    timed_out: HashMap<OfferId, Instant>,
}

impl<T> TokenPool<T> {
    /// Creates a pool with `capacity` tokens. A ticket returns its token after `ticket_timeout` at
    /// the latest.
    pub(crate) fn new(capacity: usize, ticket_timeout: Duration) -> Self {
        Self {
            capacity,
            available: capacity,
            ticket_timeout,
            tickets: HashMap::new(),
            timed_out: HashMap::new(),
        }
    }

    /// Whether a token is available.
    pub(crate) fn has_tokens(&self) -> bool {
        self.available > 0
    }

    /// The input `id`, while it is in flight.
    pub(crate) fn input(&self, id: &InputId) -> Option<&T> {
        self.tickets.get(id).map(|ticket| &ticket.input)
    }

    /// Takes a token and opens a ticket for `input`, whose id `id` its producer chose. Returns
    /// whether the input was admitted: not if no token is available, or if `id` already has a
    /// ticket.
    pub(crate) fn admit(&mut self, id: InputId, input: T, now: Instant) -> bool {
        if !self.has_tokens() || self.tickets.contains_key(&id) {
            return false;
        }
        self.available -= 1;
        self.tickets.insert(
            id,
            Ticket {
                input,
                open: HashSet::new(),
                token_held: true,
                deadline: now + self.ticket_timeout,
            },
        );
        self.check_invariant();
        true
    }

    /// Whether `offer` of the input `id` has not ended yet.
    pub(crate) fn is_offer_open(&self, id: &InputId, offer: OfferId) -> bool {
        self.tickets
            .get(id)
            .is_some_and(|t| t.open.contains(&offer))
    }

    /// Every offer that has not ended yet, with its input.
    pub(crate) fn open_offers(&self) -> impl Iterator<Item = (InputId, OfferId)> + '_ {
        self.tickets
            .iter()
            .flat_map(|(id, ticket)| ticket.open.iter().map(move |offer| (*id, *offer)))
    }

    /// Records the offers the client made for the input `id` on its own edges right after
    /// admitting it.
    pub(crate) fn add_offers(
        &mut self,
        id: &InputId,
        offers: impl IntoIterator<Item = OfferId>,
    ) -> Result<()> {
        match self.tickets.get_mut(id) {
            None => Err(Error::Internal(format!(
                "pool does not contain input id {id:?}"
            ))),
            Some(ticket) => {
                ticket.open.extend(offers);
                Ok(())
            }
        }
    }

    /// Classifies an event for an offer that is no longer open. A `Result` for an offer that
    /// recently timed out is late, and is reported `Late` only once. Anything else is `Dropped`.
    /// Decides what to do with an event for an unknown input or offer: a `Result` for an offer
    /// that timed out is late, anything else is a duplicate.
    pub(crate) fn classify_stale(&mut self, offer: OfferId, end: &OfferEnd) -> Verdict {
        let is_result = matches!(end, OfferEnd::Result { .. });
        if is_result && self.timed_out.remove(&offer).is_some() {
            Verdict::Late
        } else {
            Verdict::Dropped
        }
    }

    /// Applies the end of one offer of the input `id`, returning the token at the input's first
    /// completion, or when its last offer ends without one.
    pub(crate) fn end_offer(
        &mut self,
        id: &InputId,
        offer: OfferId,
        end: OfferEnd,
        now: Instant,
    ) -> Verdict {
        let Some(ticket) = self.tickets.get_mut(id) else {
            return self.classify_stale(offer, &end);
        };
        if !ticket.open.contains(&offer) {
            return self.classify_stale(offer, &end);
        }
        ticket.open.remove(&offer);

        // Record the offers the input continues as, and whether this ending completed the input on
        // its path.
        let path_completed = match end {
            OfferEnd::Result { children, .. } => {
                let path_completed = children.is_empty();
                ticket.open.extend(children);
                path_completed
            }
            OfferEnd::Accepted { children } => {
                ticket.open.extend(children);
                false
            }
            OfferEnd::Skipped => false,
            OfferEnd::TimedOut | OfferEnd::Disconnected => {
                self.timed_out.insert(offer, now);
                false
            }
        };

        // The token returns at the first path that completes, or when no offers are left. Clearing
        // `token_held` makes this happen only once.
        let no_offers_left = ticket.open.is_empty();
        let token_returned = ticket.token_held && (path_completed || no_offers_left);
        if token_returned {
            ticket.token_held = false;
            self.available += 1;
        }
        if no_offers_left {
            self.tickets.remove(id);
        }
        self.check_invariant();
        Verdict::Applied { token_returned }
    }

    /// Returns the tokens of tickets past their deadline, keeping the tickets so that later events
    /// still match, and forgets offers that timed out long ago. Returns how many tokens came back.
    pub(crate) fn expire(&mut self, now: Instant) -> usize {
        let mut tokens_returned = 0;
        for ticket in self.tickets.values_mut() {
            // Ticket is expired.
            if ticket.deadline <= now && ticket.token_held {
                tokens_returned += 1;
                ticket.token_held = false;
            }
        }
        self.available += tokens_returned;
        self.timed_out
            .retain(|_, ended_at| now.duration_since(*ended_at) < LATE_RESULT_WINDOW);
        self.check_invariant();
        tokens_returned
    }

    /// Checks that every token is either available or held by exactly one ticket. Does nothing in
    /// release builds.
    fn check_invariant(&self) {
        if cfg!(debug_assertions) {
            let held = self
                .tickets
                .values()
                .filter(|ticket| ticket.token_held)
                .count();
            assert_eq!(
                self.available + held,
                self.capacity,
                "every token must be available or held by one ticket"
            );
        }
    }

    /// Whether the input's ticket is at or past its deadline. False for an unknown input.
    pub(crate) fn is_past_deadline(&self, id: &InputId, now: Instant) -> bool {
        self.tickets
            .get(id)
            .is_some_and(|ticket| ticket.deadline <= now)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::routing::ids::{EdgeId, ProducerName};

    const PRODUCER: &str = "camera";
    const SESSION: u64 = 1;
    const EDGE: EdgeId = EdgeId(0);
    const ONE_TOKEN: usize = 1;
    const TWO_TOKENS: usize = 2;
    const TICKET_TIMEOUT: Duration = Duration::from_secs(10);
    const AFTER_DEADLINE: Duration = Duration::from_millis(1);

    /// A made-up offer id, distinct for each `seq`.
    fn offer(seq: u64) -> OfferId {
        OfferId {
            session: SESSION,
            edge: EDGE,
            seq,
        }
    }

    /// The engine that reports results in these tests.
    const NODE: NodeId = NodeId(0);
    /// The input kept in tickets in these tests.
    const FRAME: u32 = 5;

    /// A result that forwards the input nowhere, completing it.
    fn completed() -> OfferEnd {
        OfferEnd::Result {
            node: NODE,
            forwarded: false,
            children: vec![],
        }
    }

    fn pool(capacity: usize) -> TokenPool<u32> {
        TokenPool::new(capacity, TICKET_TIMEOUT)
    }

    /// An input id, as its producer would choose it, distinct for each `seq`.
    fn input_id(seq: u64) -> InputId {
        InputId {
            producer: ProducerName::try_from(PRODUCER).unwrap(),
            session: SESSION,
            seq,
        }
    }

    /// Admits the input `input_id(1)` into `pool` and records `offers` for it.
    fn admit_with(pool: &mut TokenPool<u32>, now: Instant, offers: &[OfferId]) -> InputId {
        assert!(pool.admit(input_id(1), FRAME, now));
        pool.add_offers(&input_id(1), offers.iter().copied())
            .unwrap();
        input_id(1)
    }

    const RETURNED: Verdict = Verdict::Applied {
        token_returned: true,
    };
    const KEPT: Verdict = Verdict::Applied {
        token_returned: false,
    };

    #[test]
    fn admit_takes_a_token_until_none_are_left() {
        let now = Instant::now();
        let mut pool = pool(TWO_TOKENS);

        assert!(pool.admit(input_id(1), FRAME, now));
        assert!(pool.admit(input_id(2), FRAME, now));
        assert!(!pool.admit(input_id(3), FRAME, now));
        pool.check_invariant();
    }

    #[test]
    fn admitted_input_is_kept_until_its_ticket_closes() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1)]);

        assert_eq!(pool.input(&id), Some(&FRAME));
        pool.end_offer(&id, offer(1), completed(), now);
        assert_eq!(pool.input(&id), None);
    }

    #[test]
    fn admitting_the_same_input_twice_is_rejected() {
        let now = Instant::now();
        let mut pool = pool(TWO_TOKENS);

        assert!(pool.admit(input_id(1), FRAME, now));
        assert!(!pool.admit(input_id(1), FRAME, now));
        assert!(pool.has_tokens());
        pool.check_invariant();
    }

    #[test]
    fn stop_returns_the_token() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1)]);

        assert_eq!(pool.end_offer(&id, offer(1), completed(), now), RETURNED);
        assert!(pool.has_tokens());
        pool.check_invariant();
    }

    #[test]
    fn pass_keeps_the_token_until_a_later_engine_completes() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1)]);

        let passed = OfferEnd::Result {
            node: NODE,
            forwarded: true,
            children: vec![offer(2)],
        };
        assert_eq!(pool.end_offer(&id, offer(1), passed, now), KEPT);
        assert!(!pool.has_tokens());

        assert_eq!(pool.end_offer(&id, offer(2), completed(), now), RETURNED);
        assert!(pool.has_tokens());
        pool.check_invariant();
    }

    #[test]
    fn fan_out_returns_the_token_only_once() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(pool.end_offer(&id, offer(1), completed(), now), RETURNED);
        assert_eq!(pool.end_offer(&id, offer(2), completed(), now), KEPT);
        pool.check_invariant();

        // Exactly one token is available: one input can be admitted, not two.
        assert!(pool.admit(input_id(2), FRAME, now));
        assert!(!pool.admit(input_id(3), FRAME, now));
    }

    #[test]
    fn skipping_one_branch_keeps_the_token_for_the_other() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(pool.end_offer(&id, offer(2), OfferEnd::Skipped, now), KEPT);
        assert!(!pool.has_tokens());

        assert_eq!(pool.end_offer(&id, offer(1), completed(), now), RETURNED);
        pool.check_invariant();
    }

    #[test]
    fn token_returns_when_every_offer_is_skipped() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(pool.end_offer(&id, offer(1), OfferEnd::Skipped, now), KEPT);
        assert_eq!(
            pool.end_offer(&id, offer(2), OfferEnd::Skipped, now),
            RETURNED
        );
        pool.check_invariant();
    }

    #[test]
    fn duplicate_event_is_dropped() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(pool.end_offer(&id, offer(1), OfferEnd::Skipped, now), KEPT);
        assert_eq!(
            pool.end_offer(&id, offer(1), OfferEnd::Skipped, now),
            Verdict::Dropped
        );
        pool.check_invariant();
    }

    #[test]
    fn event_for_a_finished_input_is_dropped() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1)]);
        pool.end_offer(&id, offer(1), completed(), now);

        assert_eq!(
            pool.end_offer(&id, offer(1), completed(), now),
            Verdict::Dropped
        );
        pool.check_invariant();
    }

    #[test]
    fn late_result_is_delivered_once() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(pool.end_offer(&id, offer(1), OfferEnd::TimedOut, now), KEPT);
        assert_eq!(
            pool.end_offer(&id, offer(1), completed(), now),
            Verdict::Late
        );
        assert_eq!(
            pool.end_offer(&id, offer(1), completed(), now),
            Verdict::Dropped
        );
        pool.check_invariant();
    }

    #[test]
    fn accepted_replaces_the_crossing_with_its_children() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1)]);

        let accepted = OfferEnd::Accepted {
            children: vec![offer(2), offer(3)],
        };
        assert_eq!(pool.end_offer(&id, offer(1), accepted, now), KEPT);
        assert_eq!(pool.end_offer(&id, offer(2), completed(), now), RETURNED);
        assert_eq!(pool.end_offer(&id, offer(3), completed(), now), KEPT);
        pool.check_invariant();
    }

    #[test]
    fn ticket_deadline_returns_the_token_but_keeps_matching_events() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1)]);

        assert_eq!(pool.expire(now + TICKET_TIMEOUT), 1);
        assert!(pool.has_tokens());

        // The ticket is still there, so the offer's end is applied, not dropped.
        assert_eq!(pool.end_offer(&id, offer(1), completed(), now), KEPT);
        pool.check_invariant();
    }

    #[test]
    fn expire_before_deadline_returns_nothing() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        admit_with(&mut pool, now, &[offer(1)]);

        assert_eq!(pool.expire(now), 0);
        assert!(!pool.has_tokens());
        pool.check_invariant();
    }

    #[test]
    fn old_timeouts_are_forgotten() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1), offer(2)]);
        pool.end_offer(&id, offer(1), OfferEnd::TimedOut, now);

        pool.expire(now + LATE_RESULT_WINDOW);

        assert_eq!(
            pool.end_offer(&id, offer(1), completed(), now),
            Verdict::Dropped
        );
    }

    #[test]
    fn offer_is_open_until_it_ends() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert!(pool.is_offer_open(&id, offer(1)));
        pool.end_offer(&id, offer(1), OfferEnd::Skipped, now);

        assert!(!pool.is_offer_open(&id, offer(1)));
        assert!(pool.is_offer_open(&id, offer(2)));
        assert!(!pool.is_offer_open(&id, offer(3)));
    }

    #[test]
    fn ticket_is_past_its_deadline_only_from_the_deadline_on() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let id = admit_with(&mut pool, now, &[offer(1)]);

        assert!(!pool.is_past_deadline(&id, now));
        assert!(!pool.is_past_deadline(&id, now + TICKET_TIMEOUT - AFTER_DEADLINE));
        assert!(pool.is_past_deadline(&id, now + TICKET_TIMEOUT));
    }

    #[test]
    fn unknown_input_is_not_past_its_deadline() {
        let now = Instant::now();
        let pool = pool(ONE_TOKEN);

        assert!(!pool.is_past_deadline(&input_id(1), now + TICKET_TIMEOUT));
    }
}
