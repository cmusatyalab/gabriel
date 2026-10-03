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
    /// Report that this offer was skipped.
    Skipped { offer: OfferId },
    /// Report that this offer's deadline passed.
    TimedOut { offer: OfferId },
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
    capacity: usize,
    timeout: Duration,
    in_flight: HashMap<OfferId, Sent<T>>,
    pending: Option<(OfferId, T)>,
    actions: VecDeque<EdgeAction<T>>,
}

impl<T: Clone> EdgeState<T> {
    pub(crate) fn new(id: EdgeId, session: u64, capacity: usize, timeout: Duration) -> Self {
        assert!(capacity > 0);
        Self {
            id,
            session,
            next_seq: 1,
            capacity,
            timeout,
            in_flight: HashMap::new(),
            pending: None,
            actions: VecDeque::new(),
        }
    }

    /// Whether an offer made now would be sent right away.
    pub(crate) fn has_spare_capacity(&self) -> bool {
        self.in_flight.len() < self.capacity
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

    fn send_pending(&mut self, now: Instant) {
        if let Some((offer, input)) = self.pending.take() {
            self.send_input_with_offer(offer, input, now);
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
        } else if let Some((skipped, _)) = self.pending.replace((offer_id, input)) {
            self.actions
                .push_back(EdgeAction::Skipped { offer: skipped });
        };
        offer_id
    }

    /// Ends a sent offer because its engine reported an outcome. Returns the input kept for it, or
    /// `None` if the offer is not in flight (a duplicate or late event).
    pub(crate) fn end(&mut self, offer: OfferId, now: Instant) -> Option<T> {
        let removed_input = self.in_flight.remove(&offer).map(|o| o.input)?;
        self.send_pending(now);
        Some(removed_input)
    }

    /// Ends every sent offer whose deadline has passed.
    pub(crate) fn expire(&mut self, now: Instant) {
        let expired_offers = self
            .in_flight
            .extract_if(|_, sent| sent.deadline <= now)
            .map(|(offer, _)| offer)
            .collect::<Vec<_>>();
        for &offer in &expired_offers {
            self.actions.push_back(EdgeAction::TimedOut { offer });
        }
        if !expired_offers.is_empty() {
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
                EdgeAction::Skipped { offer: second },
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
                EdgeAction::TimedOut { offer: first },
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
    fn offer_ids_are_unique() {
        let now = Instant::now();
        let mut edge = EdgeState::new(EDGE, SESSION, 1, TIMEOUT);

        let first = edge.offer(FRAME, now);
        let second = edge.offer(SECOND_FRAME, now);

        assert_ne!(first, second);
    }
}
