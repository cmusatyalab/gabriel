use std::{
    collections::{HashMap, VecDeque},
    time::{Duration, Instant},
};

use crate::routing::ids::{EdgeId, OfferId};

/// Actions on edge inputs.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum EdgeAction<T> {
    /// Send `input` to the edge's engine.
    Send { offer: OfferId, input: T },
    /// Report that this offer was replaced in the pending slot and never sent.
    Skipped { offer: OfferId, input: T },
    /// Report that this offer's deadline passed.
    TimedOut { offer: OfferId, input: T },
}

/// A sent offer the engine has not finished yet.
#[derive(Debug)]
struct Sent<T> {
    offer: OfferId,
    deadline: Instant,
    input: T,
}

/// Flow control state of an input edge.
#[derive(Debug)]
pub(crate) struct EdgeState<T> {
    id: EdgeId,
    session: u64,
    next_seq: u64,
    /// The most offers in flight. `None` if the owner decides when the pending offer is sent,
    /// with [`EdgeState::pull`].
    capacity: Option<usize>,
    /// Set while the engine's server cannot be reached: the edge then sends nothing, and keeps
    /// only its newest offer pending.
    suspended: bool,
    timeout: Duration,
    in_flight: HashMap<OfferId, Sent<T>>,
    pending: Option<(OfferId, T)>,
    actions: VecDeque<EdgeAction<T>>,
}

impl<T: Clone> EdgeState<T> {
    /// Creates an edge that sends an offer as soon as fewer than `capacity` are in flight. Each
    /// sent offer times out after `timeout`.
    pub(crate) fn new(id: EdgeId, session: u64, capacity: usize, timeout: Duration) -> Self {
        assert!(capacity > 0);
        Self::with_capacity(id, session, Some(capacity), timeout)
    }

    /// Creates an edge that keeps every offer pending until its owner pulls it, e.g. when the
    /// engine at its end has room. Each sent offer times out after `timeout`.
    pub(crate) fn pulled(id: EdgeId, session: u64, timeout: Duration) -> Self {
        Self::with_capacity(id, session, None, timeout)
    }

    fn with_capacity(id: EdgeId, session: u64, capacity: Option<usize>, timeout: Duration) -> Self {
        Self {
            id,
            session,
            next_seq: 1,
            capacity,
            suspended: false,
            timeout,
            in_flight: HashMap::new(),
            pending: None,
            actions: VecDeque::new(),
        }
    }

    /// Whether an offer made now would be sent right away. Never for a pulled or suspended edge.
    pub(crate) fn in_flight(&self) -> usize {
        self.in_flight.len()
    }

    pub(crate) fn has_spare_capacity(&self) -> bool {
        !self.suspended
            && self
                .capacity
                .is_some_and(|capacity| self.in_flight.len() < capacity)
    }

    /// Stops sending, e.g. while the engine's server cannot be reached. Offers in flight stay in
    /// flight until their owner ends them.
    pub(crate) fn suspend(&mut self) {
        self.suspended = true;
    }

    /// Sends again, starting with the pending offer if there is room.
    pub(crate) fn resume(&mut self, now: Instant) {
        self.suspended = false;
        self.send_pending(now);
    }

    /// Whether an offer is waiting to be sent.
    pub(crate) fn has_pending(&self) -> bool {
        self.pending.is_some()
    }

    /// Sends the pending offer, if any, whatever the capacity, and returns its id.
    pub(crate) fn pull(&mut self, now: Instant) -> Option<OfferId> {
        let (offer, input) = self.pending.take()?;
        self.send_input_with_offer(offer, input, now);
        Some(offer)
    }

    fn next_seq(&mut self) -> u64 {
        let seq = self.next_seq;
        self.next_seq += 1;
        seq
    }

    fn send_input_with_offer(&mut self, offer: OfferId, input: T, now: Instant) {
        let sent_input = Sent {
            offer,
            deadline: now + self.timeout,
            input: input.clone(),
        };
        self.actions.push_back(EdgeAction::Send { offer, input });
        self.in_flight.insert(offer, sent_input);
    }

    /// Sends the pending offer if the edge has room for it.
    fn send_pending(&mut self, now: Instant) {
        if self.has_spare_capacity() {
            self.pull(now);
        }
    }

    /// Offers `input` to the edge and returns the new offer's id.
    pub(crate) fn offer(&mut self, input: T, now: Instant) -> OfferId {
        let offer_id = OfferId {
            session: self.session,
            edge: self.id,
            seq: self.next_seq(),
        };
        if self.has_spare_capacity() {
            self.send_input_with_offer(offer_id, input, now);
        } else if let Some((skipped, skipped_input)) = self.pending.replace((offer_id, input)) {
            self.actions.push_back(EdgeAction::Skipped {
                offer: skipped,
                input: skipped_input,
            });
        }
        offer_id
    }

    /// Ends a sent offer because its engine reported an outcome. Returns the input kept for it, or
    /// `None` if the offer is not in flight (a duplicate or late event).
    pub(crate) fn end(&mut self, offer: OfferId, now: Instant) -> Option<T> {
        let removed_input = self.in_flight.remove(&offer).map(|o| o.input)?;
        self.send_pending(now);
        Some(removed_input)
    }

    /// Ends a sent offer that its engine will never answer, e.g. because the input did not reach
    /// the engine, and reports it as timed out without waiting for its deadline. Does nothing if
    /// the offer is not in flight.
    pub(crate) fn lose(&mut self, offer: OfferId, now: Instant) {
        let Some(sent) = self.in_flight.remove(&offer) else {
            return;
        };
        self.actions.push_back(EdgeAction::TimedOut {
            offer,
            input: sent.input,
        });
        self.send_pending(now);
    }

    /// Drops the pending offer, if any, and reports it as skipped.
    pub(crate) fn skip_pending(&mut self) {
        if let Some((offer, input)) = self.pending.take() {
            self.actions.push_back(EdgeAction::Skipped { offer, input });
        }
    }

    /// Ends every sent offer whose deadline has passed.
    pub(crate) fn expire(&mut self, now: Instant) {
        let expired = self
            .in_flight
            .extract_if(|_, sent| sent.deadline <= now)
            .map(|(_, sent)| sent)
            .collect::<Vec<_>>();
        let any_expired = !expired.is_empty();
        for sent in expired {
            self.actions.push_back(EdgeAction::TimedOut {
                offer: sent.offer,
                input: sent.input,
            });
        }
        if any_expired {
            self.send_pending(now);
        }
    }

    /// Returns the next action for the caller to carry out.
    pub(crate) fn poll_action(&mut self) -> Option<EdgeAction<T>> {
        self.actions.pop_front()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TIMEOUT: Duration = Duration::from_secs(1);
    const EDGE: EdgeId = EdgeId(100);
    const SESSION: u64 = 42;
    const FRAME: i32 = 5;
    const SECOND_FRAME: i32 = 6;
    const THIRD_FRAME: i32 = 7;
    const FOURTH_FRAME: i32 = 8;
    const LARGER_CAPACITY: usize = 3;

    /// Drains every action the edge has queued.
    fn actions(edge: &mut EdgeState<i32>) -> Vec<EdgeAction<i32>> {
        std::iter::from_fn(|| edge.poll_action()).collect()
    }

    #[test]
    fn offer_with_spare_capacity_is_sent() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);

        let offer = edge.offer(FRAME, now);

        assert_eq!(
            offer,
            OfferId {
                session: SESSION,
                edge: EDGE,
                seq: 1,
            }
        );
        assert_eq!(
            actions(&mut edge),
            vec![EdgeAction::Send {
                offer,
                input: FRAME
            }]
        );
        assert!(!edge.has_spare_capacity());
    }

    #[test]
    fn full_edge_keeps_newest_offer_pending_and_skips_the_older_one() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);

        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);
        edge.offer(THIRD_FRAME, now);

        assert_eq!(
            actions(&mut edge),
            vec![
                EdgeAction::Send {
                    offer: first,
                    input: FRAME
                },
                EdgeAction::Skipped {
                    offer: second,
                    input: SECOND_FRAME
                },
            ]
        );
    }

    #[test]
    fn ending_an_offer_sends_the_pending_one() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);
        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);
        actions(&mut edge);

        assert_eq!(edge.end(first, now), Some(FRAME));
        assert_eq!(
            actions(&mut edge),
            vec![EdgeAction::Send {
                offer: second,
                input: SECOND_FRAME
            }]
        );
    }

    #[test]
    fn ending_an_unknown_offer_changes_nothing() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);
        let first = edge.offer(FRAME, now);
        edge.end(first, now);
        actions(&mut edge);

        assert_eq!(edge.end(first, now), None);
        assert!(actions(&mut edge).is_empty());
        assert!(edge.has_spare_capacity());
    }

    #[test]
    fn larger_capacity_sends_that_many_before_pending() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, LARGER_CAPACITY, TIMEOUT);

        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);
        let third = edge.offer(THIRD_FRAME, now);
        edge.offer(FOURTH_FRAME, now);

        assert_eq!(
            actions(&mut edge),
            vec![
                EdgeAction::Send {
                    offer: first,
                    input: FRAME
                },
                EdgeAction::Send {
                    offer: second,
                    input: SECOND_FRAME
                },
                EdgeAction::Send {
                    offer: third,
                    input: THIRD_FRAME
                },
            ]
        );
        assert!(!edge.has_spare_capacity());
    }

    #[test]
    fn expired_offer_times_out_and_sends_pending() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);
        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);
        actions(&mut edge);

        edge.expire(now + TIMEOUT);

        assert_eq!(
            actions(&mut edge),
            vec![
                EdgeAction::TimedOut {
                    offer: first,
                    input: FRAME
                },
                EdgeAction::Send {
                    offer: second,
                    input: SECOND_FRAME
                },
            ]
        );
        assert_eq!(edge.end(first, now + TIMEOUT), None);
    }

    #[test]
    fn expire_before_deadline_does_nothing() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);
        let first = edge.offer(FRAME, now);
        actions(&mut edge);

        edge.expire(now);

        assert!(actions(&mut edge).is_empty());
        assert_eq!(edge.end(first, now), Some(FRAME));
    }

    #[test]
    fn pulled_edge_keeps_only_the_newest_offer_until_pulled() {
        let now = Instant::now();
        let mut edge = EdgeState::pulled(EDGE, SESSION, TIMEOUT);

        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);

        assert_eq!(
            actions(&mut edge),
            vec![EdgeAction::Skipped {
                offer: first,
                input: FRAME
            }]
        );
        assert_eq!(edge.pull(now), Some(second));
        assert_eq!(
            actions(&mut edge),
            vec![EdgeAction::Send {
                offer: second,
                input: SECOND_FRAME
            }]
        );
        assert_eq!(edge.pull(now), None);
    }

    #[test]
    fn ending_an_offer_on_a_pulled_edge_does_not_send_the_pending_one() {
        let now = Instant::now();
        let mut edge = EdgeState::pulled(EDGE, SESSION, TIMEOUT);
        edge.offer(FRAME, now);
        let first = edge.pull(now).unwrap();
        edge.offer(SECOND_FRAME, now);
        actions(&mut edge);

        assert_eq!(edge.end(first, now), Some(FRAME));
        edge.expire(now + TIMEOUT);

        assert!(actions(&mut edge).is_empty());
        assert!(edge.has_pending());
    }

    #[test]
    fn lost_offer_times_out_at_once_and_frees_its_capacity() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);
        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);
        actions(&mut edge);

        edge.lose(first, now);

        assert_eq!(
            actions(&mut edge),
            vec![
                EdgeAction::TimedOut {
                    offer: first,
                    input: FRAME
                },
                EdgeAction::Send {
                    offer: second,
                    input: SECOND_FRAME
                },
            ]
        );
        edge.lose(first, now);
        assert!(actions(&mut edge).is_empty());
    }

    #[test]
    fn skipping_the_pending_offer_reports_it() {
        let now = Instant::now();
        let mut edge = EdgeState::pulled(EDGE, SESSION, TIMEOUT);
        let first = edge.offer(FRAME, now);

        edge.skip_pending();

        assert_eq!(
            actions(&mut edge),
            vec![EdgeAction::Skipped {
                offer: first,
                input: FRAME
            }]
        );
        assert!(!edge.has_pending());
    }

    #[test]
    fn suspended_edge_sends_nothing_until_resumed() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);
        edge.suspend();

        edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);
        assert!(!edge.has_spare_capacity());
        assert!(matches!(
            actions(&mut edge)[..],
            [EdgeAction::Skipped { input: FRAME, .. }]
        ));

        edge.resume(now);
        assert_eq!(
            actions(&mut edge),
            vec![EdgeAction::Send {
                offer: second,
                input: SECOND_FRAME
            }]
        );
    }

    #[test]
    fn offer_ids_are_unique() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);

        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);

        assert_ne!(first, second);
    }
}
