use std::{
    collections::{HashMap, HashSet},
    time::{Duration, Instant},
};

use crate::Result;
use crate::{
    Error,
    routing::ids::{InputId, OfferId},
};

/// How long the client remembers an offer that timed out, so that a late `Result` for it is told
/// apart from a duplicate.
const LATE_RESULT_WINDOW: Duration = Duration::from_secs(60);

/// How an offer ended, as reported to the client.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum OfferEnd {
    /// The engine reported an outcome. `children` are the offers it created: the server's, plus
    /// the client's own (e.g. an uplink after `Pass`). No children means the input completed on
    /// this path.
    Result { children: Vec<OfferId> },
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
struct Ticket {
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
pub(crate) struct TokenPool {
    capacity: usize,
    available: usize,
    ticket_timeout: Duration,
    tickets: HashMap<InputId, Ticket>,
    /// Offers that ended with TimedOut or Disconnected, and when.
    timed_out: HashMap<OfferId, Instant>,
}

impl TokenPool {
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

    /// Takes a token and opens a ticket for `input`, whose id its producer chose. Returns whether
    /// the input was admitted: not if no token is available, or if `input` already has a ticket.
    pub(crate) fn admit(&mut self, input: InputId, now: Instant) -> bool {
        if !self.has_tokens() || self.tickets.contains_key(&input) {
            return false;
        }
        self.available -= 1;
        self.tickets.insert(
            input,
            Ticket {
                open: HashSet::new(),
                token_held: true,
                deadline: now + self.ticket_timeout,
            },
        );
        self.check_invariant();
        true
    }

    /// Records the offers the client made for `input` on its own edges right after admitting it.
    pub(crate) fn add_offers(
        &mut self,
        input: &InputId,
        offers: impl IntoIterator<Item = OfferId>,
    ) -> Result<()> {
        match self.tickets.get_mut(input) {
            None => Err(Error::Internal(format!(
                "pool does not contain input id {input:?}"
            ))),
            Some(ticket) => {
                ticket.open.extend(offers);
                Ok(())
            }
        }
    }

    /// Decides what to do with an event for an unknown input or offer: a `Result` for an offer
    /// that timed out is late, anything else is a duplicate.
    fn late_or_dropped(&mut self, offer: OfferId, end: &OfferEnd) -> Verdict {
        let is_result = matches!(end, OfferEnd::Result { .. });
        if is_result && self.timed_out.remove(&offer).is_some() {
            Verdict::Late
        } else {
            Verdict::Dropped
        }
    }

    /// Applies the end of one offer of `input`, returning the token at the input's first
    /// completion, or when its last offer ends without one.
    pub(crate) fn end_offer(
        &mut self,
        input: &InputId,
        offer: OfferId,
        end: OfferEnd,
        now: Instant,
    ) -> Verdict {
        let Some(ticket) = self.tickets.get_mut(input) else {
            return self.late_or_dropped(offer, &end);
        };
        if !ticket.open.contains(&offer) {
            return self.late_or_dropped(offer, &end);
        }
        ticket.open.remove(&offer);

        // Record the offers the input continues as, and whether this ending completed the input on
        // its path.
        let path_completed = match end {
            OfferEnd::Result { children } => {
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
            self.tickets.remove(input);
        }
        self.check_invariant();
        Verdict::Applied { token_returned }
    }

    /// Returns the tokens of tickets past their deadline, keeping the tickets so that later events
    /// still match, and forgets offers that timed out long ago. Returns how many tokens came back.
    pub(crate) fn expire(&mut self, now: Instant) -> usize {
        let mut tokens_returned = 0;
        for ticket in self.tickets.values_mut() {
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
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::routing::ids::EdgeId;

    const PRODUCER: &str = "camera";
    const SESSION: u64 = 1;
    const EDGE: EdgeId = EdgeId(0);
    const ONE_TOKEN: usize = 1;
    const TWO_TOKENS: usize = 2;
    const TICKET_TIMEOUT: Duration = Duration::from_secs(10);

    /// A made-up offer id, distinct for each `seq`.
    fn offer(seq: u64) -> OfferId {
        OfferId {
            session: SESSION,
            edge: EDGE,
            seq,
        }
    }

    fn completed() -> OfferEnd {
        OfferEnd::Result { children: vec![] }
    }

    fn pool(capacity: usize) -> TokenPool {
        TokenPool::new(capacity, TICKET_TIMEOUT)
    }

    /// An input id, as its producer would choose it, distinct for each `seq`.
    fn input_id(seq: u64) -> InputId {
        InputId {
            producer: String::from(PRODUCER),
            session: SESSION,
            seq,
        }
    }

    /// Admits the input `input_id(1)` into `pool` and records `offers` for it.
    fn admit_with(pool: &mut TokenPool, now: Instant, offers: &[OfferId]) -> InputId {
        assert!(pool.admit(input_id(1), now));
        pool.add_offers(&input_id(1), offers.iter().copied()).unwrap();
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

        assert!(pool.admit(input_id(1), now));
        assert!(pool.admit(input_id(2), now));
        assert!(!pool.admit(input_id(3), now));
        pool.check_invariant();
    }

    #[test]
    fn admitting_the_same_input_twice_is_rejected() {
        let now = Instant::now();
        let mut pool = pool(TWO_TOKENS);

        assert!(pool.admit(input_id(1), now));
        assert!(!pool.admit(input_id(1), now));
        assert!(pool.has_tokens());
        pool.check_invariant();
    }

    #[test]
    fn stop_returns_the_token() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1)]);

        assert_eq!(
            pool.end_offer(&input, offer(1), completed(), now),
            RETURNED
        );
        assert!(pool.has_tokens());
        pool.check_invariant();
    }

    #[test]
    fn pass_keeps_the_token_until_a_later_engine_completes() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1)]);

        let passed = OfferEnd::Result {
            children: vec![offer(2)],
        };
        assert_eq!(pool.end_offer(&input, offer(1), passed, now), KEPT);
        assert!(!pool.has_tokens());

        assert_eq!(
            pool.end_offer(&input, offer(2), completed(), now),
            RETURNED
        );
        assert!(pool.has_tokens());
        pool.check_invariant();
    }

    #[test]
    fn fan_out_returns_the_token_only_once() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(
            pool.end_offer(&input, offer(1), completed(), now),
            RETURNED
        );
        assert_eq!(pool.end_offer(&input, offer(2), completed(), now), KEPT);
        pool.check_invariant();

        // Exactly one token is available: one input can be admitted, not two.
        assert!(pool.admit(input_id(2), now));
        assert!(!pool.admit(input_id(3), now));
    }

    #[test]
    fn skipping_one_branch_keeps_the_token_for_the_other() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(
            pool.end_offer(&input, offer(2), OfferEnd::Skipped, now),
            KEPT
        );
        assert!(!pool.has_tokens());

        assert_eq!(
            pool.end_offer(&input, offer(1), completed(), now),
            RETURNED
        );
        pool.check_invariant();
    }

    #[test]
    fn token_returns_when_every_offer_is_skipped() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(
            pool.end_offer(&input, offer(1), OfferEnd::Skipped, now),
            KEPT
        );
        assert_eq!(
            pool.end_offer(&input, offer(2), OfferEnd::Skipped, now),
            RETURNED
        );
        pool.check_invariant();
    }

    #[test]
    fn duplicate_event_is_dropped() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(
            pool.end_offer(&input, offer(1), OfferEnd::Skipped, now),
            KEPT
        );
        assert_eq!(
            pool.end_offer(&input, offer(1), OfferEnd::Skipped, now),
            Verdict::Dropped
        );
        pool.check_invariant();
    }

    #[test]
    fn event_for_a_finished_input_is_dropped() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1)]);
        pool.end_offer(&input, offer(1), completed(), now);

        assert_eq!(
            pool.end_offer(&input, offer(1), completed(), now),
            Verdict::Dropped
        );
        pool.check_invariant();
    }

    #[test]
    fn late_result_is_delivered_once() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1), offer(2)]);

        assert_eq!(
            pool.end_offer(&input, offer(1), OfferEnd::TimedOut, now),
            KEPT
        );
        assert_eq!(
            pool.end_offer(&input, offer(1), completed(), now),
            Verdict::Late
        );
        assert_eq!(
            pool.end_offer(&input, offer(1), completed(), now),
            Verdict::Dropped
        );
        pool.check_invariant();
    }

    #[test]
    fn accepted_replaces_the_crossing_with_its_children() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1)]);

        let accepted = OfferEnd::Accepted {
            children: vec![offer(2), offer(3)],
        };
        assert_eq!(pool.end_offer(&input, offer(1), accepted, now), KEPT);
        assert_eq!(
            pool.end_offer(&input, offer(2), completed(), now),
            RETURNED
        );
        assert_eq!(pool.end_offer(&input, offer(3), completed(), now), KEPT);
        pool.check_invariant();
    }

    #[test]
    fn ticket_deadline_returns_the_token_but_keeps_matching_events() {
        let now = Instant::now();
        let mut pool = pool(ONE_TOKEN);
        let input = admit_with(&mut pool, now, &[offer(1)]);

        assert_eq!(pool.expire(now + TICKET_TIMEOUT), 1);
        assert!(pool.has_tokens());

        // The ticket is still there, so the offer's end is applied, not dropped.
        assert_eq!(pool.end_offer(&input, offer(1), completed(), now), KEPT);
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
        let input = admit_with(&mut pool, now, &[offer(1), offer(2)]);
        pool.end_offer(&input, offer(1), OfferEnd::TimedOut, now);

        pool.expire(now + LATE_RESULT_WINDOW);

        assert_eq!(
            pool.end_offer(&input, offer(1), completed(), now),
            Verdict::Dropped
        );
    }
}
