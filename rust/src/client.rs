//! Client: takes inputs from local producers, and routes them through the flows it added. A
//! flow's engines run on the local server, and on the remote server if the client connects to one.
//! It publishes what happens to each producer's inputs for the producer's app, which reads it with
//! a [`Subscription`](crate::Subscription). Its work runs on its own thread.
mod compile;
mod local_server;
mod producers;
mod remote_server;
mod server_link;
mod worker;

use std::{net::SocketAddr, sync::mpsc, thread::JoinHandle};

use crate::{Error, Result, client::worker::Command, services, spec::FlowSpec};

/// How many inputs a crossing to the remote server may have in flight, unless the client is told
/// otherwise: the least that keeps the server's pending slots fresh ("Freshness" in the design
/// note). A longer round trip may need more to keep the remote engines busy.
pub const DEFAULT_REMOTE_CROSSING_CAPACITY: usize = services::LOCAL_CROSSING_CAPACITY;

/// How a client reaches the remote server.
#[derive(Debug, Clone)]
pub struct RemoteServerConfig {
    /// The server's address, as [`Server::remote_addr`](crate::Server::remote_addr) gives it.
    pub addr: SocketAddr,
    /// The only certificate the client trusts, in DER, as
    /// [`Server::certificate`](crate::Server::certificate) gives it.
    pub certificate: Vec<u8>,
    /// How many inputs each crossing to the remote server may have in flight: on the network, or
    /// waiting at the server.
    pub crossing_capacity: usize,
}

impl RemoteServerConfig {
    /// The remote server at `addr`, with its `certificate`, and the default crossing capacity.
    pub fn new(addr: SocketAddr, certificate: Vec<u8>) -> Self {
        Self {
            addr,
            certificate,
            crossing_capacity: DEFAULT_REMOTE_CROSSING_CAPACITY,
        }
    }
}

/// Bridges local producers to the servers that run a flow's engines.
#[derive(Debug)]
pub struct Client {
    commands: Option<mpsc::Sender<Command>>,
    thread: Option<JoinHandle<Result<()>>>,
}

impl Client {
    /// Creates a client named `name` that talks to the local server `local_server`, and to the
    /// remote server `remote` if it is given. Both servers may start later; the client connects
    /// to the remote server, and connects again after a loss, on its own.
    pub fn new(name: &str, local_server: &str, remote: Option<RemoteServerConfig>) -> Result<Self> {
        if let Some(remote) = &remote
            && remote.crossing_capacity == 0
        {
            return Err(Error::InvalidArgument(String::from(
                "crossings to the remote server need a capacity of at least 1",
            )));
        }
        let (commands, thread) = worker::spawn(name, local_server, remote)?;
        Ok(Self {
            commands: Some(commands),
            thread: Some(thread),
        })
    }

    /// Starts taking inputs from the local producer `name`.
    pub fn add_producer(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::AddProducer {
            name: name.to_string(),
            reply,
        })
    }

    /// Stops taking inputs from the producer `name`. Fails while a flow uses it.
    pub fn remove_producer(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::RemoveProducer {
            name: name.to_string(),
            reply,
        })
    }

    /// Adds the flow `name` described by `spec`: installs its engines' parts on the servers, and
    /// routes the inputs its producers publish on `name`. Its producers must be added first. The
    /// local part must install. If the remote server is not connected yet, the remote part is
    /// installed once it is; until then, inputs bound for it wait at the client, the newest
    /// replacing the rest.
    pub fn add_flow(&mut self, name: &str, spec: &FlowSpec) -> Result<()> {
        self.request(|reply| Command::AddFlow {
            name: name.to_string(),
            spec: spec.clone(),
            reply,
        })
    }

    /// Removes the flow `name` from the client and from the servers.
    pub fn remove_flow(&mut self, name: &str) -> Result<()> {
        self.request(|reply| Command::RemoveFlow {
            name: name.to_string(),
            reply,
        })
    }

    /// Sends a command to the client thread and waits for its reply.
    fn request(&self, command: impl FnOnce(mpsc::Sender<Result<()>>) -> Command) -> Result<()> {
        let (reply, reply_rx) = mpsc::channel();

        let stopped = || Error::Internal(String::from("client thread stopped"));
        match self.commands.as_ref() {
            None => return Err(stopped()),
            Some(commands) => commands.send(command(reply)).map_err(|_| stopped())?,
        }
        reply_rx.recv().map_err(|_| stopped())?
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        // Dropping the only command sender disconnects the channel, which makes the client thread
        // remove its flows and return from its loop. Then wait for it to finish.
        drop(self.commands.take());
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use std::{
        collections::HashMap,
        sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
        },
        thread,
        time::{Duration, Instant},
    };

    use crate::{
        Client, Engine, Error, EventKind, Producer, RemoteServerConfig, Server, Subscription,
        Timestamp,
        spec::{EdgeSpec, EngineSpec, FlowSpec, Location, ProducerSpec},
        subscription::{Event, Published},
        test_util::{FLOW, INPUT_LEN, MAX_INPUT_SIZE, publish, unique},
    };

    const MAX_OUTPUT_SIZE: usize = 16;
    const OFFER_TIMEOUT: Duration = Duration::from_secs(5);
    /// Far longer than anything here takes, so a test fails rather than hangs.
    const WAIT: Duration = Duration::from_secs(5);
    /// Long enough for an input to reach an engine if it were going to.
    const QUIET: Duration = Duration::from_millis(200);
    const ONE_TOKEN: usize = 1;

    /// A local server, a client of it, and a producer the client takes inputs from.
    struct Fixture {
        server: Server,
        server_name: String,
        client: Client,
        producer: Producer,
        producer_name: String,
    }

    impl Fixture {
        fn new() -> Self {
            let server_name = unique("server");
            let server = Server::new(&server_name, None).unwrap();
            let mut client = Client::new(&unique("client"), &server_name, None).unwrap();
            let producer_name = unique("camera");
            let producer = Producer::new(&producer_name, MAX_INPUT_SIZE).unwrap();
            client.add_producer(&producer_name).unwrap();
            Self {
                server,
                server_name,
                client,
                producer,
                producer_name,
            }
        }

        /// Subscribes to the events about the producer's inputs.
        fn subscribe(&self) -> Subscription {
            Subscription::producer(&self.producer_name).unwrap()
        }

        /// Starts the local engine `name`, and serves it.
        fn engine(&mut self, name: &str) -> Engine {
            let engine = Engine::new(name, None, MAX_OUTPUT_SIZE).unwrap();
            self.server.add_engine(name).unwrap();
            engine
        }

        /// A flow where the producer, with `tokens` tokens, feeds the first engine of `chain`,
        /// and each engine feeds the next.
        fn chain(&self, tokens: usize, chain: &[&str]) -> FlowSpec {
            let mut from = self.producer_name.clone();
            let mut edges = Vec::new();
            for &engine in chain {
                edges.push(EdgeSpec {
                    from,
                    to: String::from(engine),
                    timeout: OFFER_TIMEOUT,
                });
                from = String::from(engine);
            }
            FlowSpec {
                producers: vec![ProducerSpec {
                    name: self.producer_name.clone(),
                    tokens,
                }],
                engines: chain
                    .iter()
                    .map(|&name| EngineSpec {
                        name: String::from(name),
                        location: Location::Local,
                    })
                    .collect(),
                edges,
            }
        }
    }

    /// An event as the tests compare it: its kind, its input's sequence number, its engine, and
    /// its result.
    type Seen = (EventKind, u64, String, Vec<u8>);

    /// Reads the next event with `read`, if one arrives within `timeout`. Fails on lost events.
    fn try_next<T>(
        subscription: &mut Subscription,
        timeout: Duration,
        read: impl Fn(&Published<'_>) -> T,
    ) -> Option<T> {
        let deadline = Instant::now() + timeout;
        loop {
            match subscription.poll() {
                Ok(Event::Published(event)) => return Some(read(&event)),
                Ok(Event::Lost { count }) => panic!("{count} events were lost"),
                Err(Error::Again) => {}
                Err(e) => panic!("{e}"),
            }
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return None;
            }
            let _ = subscription.wait(left);
        }
    }

    /// Returns the next event, if one arrives within `timeout`. Fails on lost events.
    fn try_next_event(subscription: &mut Subscription, timeout: Duration) -> Option<Seen> {
        try_next(subscription, timeout, |event| {
            (
                event.kind(),
                event.id().seq(),
                event.engine().to_owned(),
                event.result().to_vec(),
            )
        })
    }

    fn next_event(subscription: &mut Subscription) -> Seen {
        try_next_event(subscription, WAIT).expect("no event arrived")
    }

    /// Returns the next event's kind, when it happened, and when its offer was made.
    fn next_stamps(subscription: &mut Subscription) -> (EventKind, Timestamp, Option<Timestamp>) {
        try_next(subscription, WAIT, |event| {
            (event.kind(), event.at(), event.offered_at())
        })
        .expect("no event arrived")
    }

    /// Waits for the engine's next input, and returns its first byte.
    fn next_input(engine: &mut Engine) -> u8 {
        engine.wait(WAIT).unwrap();
        engine.poll_input().unwrap().data[0]
    }

    #[test]
    fn producer_input_reaches_a_local_engine() {
        let mut fixture = Fixture::new();
        let detector_name = unique("detector");
        let mut detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        publish(&mut fixture.producer, 7);

        detector.wait(WAIT).unwrap();
        let input = detector.poll_input().unwrap();
        assert_eq!(input.data, [7; INPUT_LEN]);
        assert_eq!(input.id.producer.to_string(), fixture.producer_name);
    }

    #[test]
    fn flow_from_a_toml_spec_reaches_its_engine() {
        let mut fixture = Fixture::new();
        let detector_name = unique("detector");
        let mut detector = fixture.engine(&detector_name);
        let spec: FlowSpec = format!(
            r#"
            [[producers]]
            name = "{producer}"
            tokens = 1

            [[engines]]
            name = "{detector_name}"
            location = "local"

            [[edges]]
            from = "{producer}"
            to = "{detector_name}"
            timeout = "5s"
            "#,
            producer = fixture.producer_name,
        )
        .parse()
        .unwrap();
        fixture.client.add_flow(FLOW, &spec).unwrap();

        publish(&mut fixture.producer, 7);

        assert_eq!(next_input(&mut detector), 7);
    }

    #[test]
    fn result_returns_the_token_for_the_newest_input() {
        let mut fixture = Fixture::new();
        let detector_name = unique("detector");
        let mut detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();
        publish(&mut fixture.producer, 1);
        assert_eq!(next_input(&mut detector), 1);

        // The only token is with input 1, so inputs 2 and 3 wait at the client, where 3 replaces
        // 2.
        publish(&mut fixture.producer, 2);
        publish(&mut fixture.producer, 3);
        detector.stop(&[]).unwrap();

        assert_eq!(next_input(&mut detector), 3);
    }

    #[test]
    fn filter_passes_its_input_to_the_next_engine() {
        let mut fixture = Fixture::new();
        let (filter_name, detector_name) = (unique("filter"), unique("detector"));
        let mut filter = fixture.engine(&filter_name);
        let mut detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&filter_name, &detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        publish(&mut fixture.producer, 1);
        assert_eq!(next_input(&mut filter), 1);
        filter.pass(&[]).unwrap();
        assert_eq!(next_input(&mut detector), 1);

        // The token stays with input 1 until the detector is done with it.
        publish(&mut fixture.producer, 2);
        assert!(filter.wait(QUIET).is_err());
        detector.stop(&[]).unwrap();
        assert_eq!(next_input(&mut filter), 2);
    }

    #[test]
    fn events_tell_when_each_stage_happened() {
        let mut fixture = Fixture::new();
        let mut subscription = fixture.subscribe();
        let (filter_name, detector_name) = (unique("filter"), unique("detector"));
        let mut filter = fixture.engine(&filter_name);
        let mut detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&filter_name, &detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        let published_at = Timestamp::now();
        publish(&mut fixture.producer, 1);
        next_input(&mut filter);
        filter.pass(&[]).unwrap();
        next_input(&mut detector);
        detector.stop(&[]).unwrap();

        let stamps: Vec<_> = (0..4).map(|_| next_stamps(&mut subscription)).collect();
        let kinds: Vec<_> = stamps.iter().map(|(kind, ..)| *kind).collect();
        assert_eq!(
            kinds,
            [
                EventKind::Sent,
                EventKind::Result,
                EventKind::Sent,
                EventKind::Result
            ]
        );
        // Each offer is made before it is sent, and sent before its result.
        let times: Vec<_> = stamps
            .iter()
            .map(|(_, at, offered_at)| (offered_at.expect("the server made the offer"), *at))
            .collect();
        assert!(published_at <= times[0].0);
        assert!(times[0].0 <= times[0].1 && times[0].1 <= times[1].1);
        // The filter's result made the detector's offer.
        assert_eq!(times[2].0, times[3].0);
        assert!(times[1].1 <= times[2].0 && times[2].0 <= times[2].1);
        assert!(times[2].1 <= times[3].1);
    }

    #[test]
    fn removed_flow_takes_no_more_inputs() {
        let mut fixture = Fixture::new();
        let detector_name = unique("detector");
        let detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        fixture.client.remove_flow(FLOW).unwrap();
        publish(&mut fixture.producer, 1);

        assert!(detector.wait(QUIET).is_err());
        // The flow no longer uses the engine, so it can be removed.
        fixture.server.remove_engine(&detector_name).unwrap();
    }

    #[test]
    fn dropped_client_removes_its_flows() {
        let mut fixture = Fixture::new();
        let detector_name = unique("detector");
        let _detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        let Fixture {
            mut server, client, ..
        } = fixture;
        drop(client);

        server.remove_engine(&detector_name).unwrap();
    }

    #[test]
    fn app_reads_what_happened_to_its_inputs() {
        const RESULT: [u8; 2] = [4, 2];
        let mut fixture = Fixture::new();
        let mut subscription = fixture.subscribe();
        let detector_name = unique("detector");
        let mut detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        publish(&mut fixture.producer, 1);
        assert_eq!(next_input(&mut detector), 1);
        detector.stop(&RESULT).unwrap();

        assert_eq!(
            next_event(&mut subscription),
            (EventKind::Sent, 1, detector_name.clone(), vec![])
        );
        assert_eq!(
            next_event(&mut subscription),
            (EventKind::Result, 1, detector_name, RESULT.to_vec())
        );
    }

    #[test]
    fn input_replaced_at_the_server_is_reported_skipped() {
        const TOKENS: usize = 3;
        let mut fixture = Fixture::new();
        let mut subscription = fixture.subscribe();
        let detector_name = unique("detector");
        let mut detector = fixture.engine(&detector_name);
        let spec = fixture.chain(TOKENS, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();
        publish(&mut fixture.producer, 1);
        assert_eq!(next_input(&mut detector), 1);
        assert_eq!(next_event(&mut subscription).0, EventKind::Sent);

        // The detector is busy, so at the server each new input replaces the one waiting in the
        // detector's pending slot. An input the client already replaced never enters the flow, so
        // which input is skipped depends on timing.
        let skipped = loop {
            publish(&mut fixture.producer, 2);
            if let Some(event) = try_next_event(&mut subscription, QUIET) {
                break event;
            }
        };

        assert_eq!(skipped.0, EventKind::Skipped);
        assert!(skipped.1 > 1);
        assert_eq!(skipped.2, detector_name);
    }

    /// Runs the engine `name` on its own thread until `stop` is set: it answers every input after
    /// `work`, passing on every second one.
    fn run_engine(name: String, work: Duration, stop: Arc<AtomicBool>) -> thread::JoinHandle<()> {
        thread::spawn(move || {
            let mut engine = Engine::new(&name, None, MAX_OUTPUT_SIZE).unwrap();
            while !stop.load(Ordering::Relaxed) {
                if engine.wait(QUIET).is_err() {
                    continue;
                }
                let Ok(input) = engine.poll_input() else {
                    continue;
                };
                let passes = input.data[0] % 2 == 0;
                thread::sleep(work);
                if passes {
                    engine.pass(&[]).unwrap();
                } else {
                    engine.stop(&[]).unwrap();
                }
            }
        })
    }

    #[test]
    fn every_offer_the_app_hears_of_ends_exactly_once() {
        const TOKENS: usize = 2;
        const FRAMES: u8 = 200;
        const FRAME_INTERVAL: Duration = Duration::from_millis(1);
        const FILTER_WORK: Duration = Duration::from_millis(2);
        const DETECTOR_WORK: Duration = Duration::from_millis(7);
        let mut fixture = Fixture::new();
        let mut subscription = fixture.subscribe();
        let (filter_name, detector_name) = (unique("filter"), unique("detector"));
        fixture.server.add_engine(&filter_name).unwrap();
        fixture.server.add_engine(&detector_name).unwrap();
        let stop = Arc::new(AtomicBool::new(false));
        let engines = [
            run_engine(filter_name.clone(), FILTER_WORK, stop.clone()),
            run_engine(detector_name.clone(), DETECTOR_WORK, stop.clone()),
        ];
        let spec = fixture.chain(TOKENS, &[&filter_name, &detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        let mut events = Vec::new();
        for frame in 0..FRAMES {
            publish(&mut fixture.producer, frame);
            while let Some(event) = try_next_event(&mut subscription, FRAME_INTERVAL) {
                events.push(event);
            }
        }
        while let Some(event) = try_next_event(&mut subscription, QUIET) {
            events.push(event);
        }
        stop.store(true, Ordering::Relaxed);
        for engine in engines {
            engine.join().unwrap();
        }

        // Each input reaches each engine at most once in this flow, so an offer is known by its
        // input and engine. Sent may come first; then exactly one event ends the offer.
        let mut ends: HashMap<(u64, String), Vec<EventKind>> = HashMap::new();
        for (kind, seq, engine, _) in events {
            ends.entry((seq, engine)).or_default().push(kind);
        }
        assert!(ends.keys().any(|(_, engine)| *engine == detector_name));
        for (offer, kinds) in &ends {
            let ending = kinds.strip_prefix(&[EventKind::Sent]).unwrap_or(kinds);
            assert!(
                matches!(
                    ending,
                    [EventKind::Result | EventKind::Skipped | EventKind::TimedOut]
                ),
                "offer {offer:?} had events {kinds:?}"
            );
            if kinds[0] == EventKind::Sent {
                assert_ne!(ending, [EventKind::Skipped], "offer {offer:?} was sent");
            }
        }
    }

    #[test]
    fn restarted_server_disconnects_and_gets_the_flow_again() {
        let mut fixture = Fixture::new();
        let mut subscription = fixture.subscribe();
        let detector_name = unique("detector");
        let mut detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();
        publish(&mut fixture.producer, 1);
        assert_eq!(next_input(&mut detector), 1);
        assert_eq!(next_event(&mut subscription).0, EventKind::Sent);

        // The server stops while the detector holds input 1, and a new one starts.
        let Fixture {
            server,
            server_name,
            client: _client,
            mut producer,
            ..
        } = fixture;
        drop(server);
        let mut server = Server::new(&server_name, None).unwrap();
        server.add_engine(&detector_name).unwrap();

        // Input 1's offer ends with the connection, so its token comes back.
        let disconnected = next_event(&mut subscription);
        assert_eq!(disconnected.0, EventKind::Disconnected);
        // The answer reaches the new server, which never gave the detector this input.
        detector.stop(&[]).unwrap();
        // The flow is installed on the new server, so a new input reaches the detector.
        let deadline = Instant::now() + WAIT;
        let input = loop {
            publish(&mut producer, 2);
            if detector.wait(QUIET).is_ok()
                && let Ok(input) = detector.poll_input()
            {
                break input.data[0];
            }
            assert!(
                Instant::now() < deadline,
                "the flow was not installed again"
            );
        };
        assert_eq!(input, 2);
    }

    #[test]
    fn flow_with_an_unserved_engine_is_refused() {
        let mut fixture = Fixture::new();
        let spec = fixture.chain(ONE_TOKEN, &[&unique("detector")]);

        let result = fixture.client.add_flow(FLOW, &spec);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
        // Nothing was added, so the flow can be added once the engine is served.
        let detector_name = spec.engines[0].name.clone();
        let _detector = fixture.engine(&detector_name);
        fixture.client.add_flow(FLOW, &spec).unwrap();
    }

    #[test]
    fn flow_needs_its_producers_added() {
        let mut fixture = Fixture::new();
        let detector_name = unique("detector");
        let _detector = fixture.engine(&detector_name);
        let mut spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        spec.producers[0].name = unique("lidar");
        spec.edges[0].from = spec.producers[0].name.clone();

        let result = fixture.client.add_flow(FLOW, &spec);

        assert!(matches!(result, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn producer_used_by_a_flow_cannot_be_removed() {
        let mut fixture = Fixture::new();
        let detector_name = unique("detector");
        let _detector = fixture.engine(&detector_name);
        let spec = fixture.chain(ONE_TOKEN, &[&detector_name]);
        fixture.client.add_flow(FLOW, &spec).unwrap();
        let producer_name = fixture.producer_name.clone();

        assert!(fixture.client.remove_producer(&producer_name).is_err());
        fixture.client.remove_flow(FLOW).unwrap();
        fixture.client.remove_producer(&producer_name).unwrap();
    }

    #[test]
    fn flow_fails_without_a_running_server() {
        let mut client = Client::new(&unique("client"), &unique("server"), None).unwrap();
        let producer_name = unique("camera");
        client.add_producer(&producer_name).unwrap();
        let spec = FlowSpec {
            producers: vec![ProducerSpec {
                name: producer_name.clone(),
                tokens: ONE_TOKEN,
            }],
            engines: vec![EngineSpec {
                name: String::from("detector"),
                location: Location::Local,
            }],
            edges: vec![EdgeSpec {
                from: producer_name,
                to: String::from("detector"),
                timeout: OFFER_TIMEOUT,
            }],
        };

        assert!(matches!(
            client.add_flow(FLOW, &spec),
            Err(Error::Internal(_))
        ));
    }

    #[test]
    fn add_and_remove_producer_reject_duplicates_and_unknown_names() {
        let mut client = Client::new(&unique("client"), &unique("server"), None).unwrap();
        let producer_name = unique("producer");

        client.add_producer(&producer_name).unwrap();
        assert!(matches!(
            client.add_producer(&producer_name),
            Err(Error::InvalidArgument(_))
        ));
        client.remove_producer(&producer_name).unwrap();
        assert!(matches!(
            client.remove_producer(&producer_name),
            Err(Error::InvalidArgument(_))
        ));
    }

    /// A local and a remote server, a client of both, and a producer the client takes inputs from.
    struct Offload {
        local: Server,
        remote: Server,
        client: Client,
        producer: Producer,
        producer_name: String,
    }

    impl Offload {
        fn new() -> Self {
            let local_name = unique("server");
            let local = Server::new(&local_name, None).unwrap();
            let remote = Server::new(&unique("remote"), Some("127.0.0.1:0")).unwrap();
            let config = RemoteServerConfig::new(
                remote.remote_addr().unwrap(),
                remote.certificate().unwrap().to_vec(),
            );
            let mut client = Client::new(&unique("client"), &local_name, Some(config)).unwrap();
            let producer_name = unique("camera");
            let producer = Producer::new(&producer_name, MAX_INPUT_SIZE).unwrap();
            client.add_producer(&producer_name).unwrap();
            Self {
                local,
                remote,
                client,
                producer,
                producer_name,
            }
        }

        /// The producer feeds `filter` on the device, which feeds `detector` on the remote server.
        fn spec(&self, filter: &str, detector: &str) -> FlowSpec {
            let edge = |from: &str, to: &str| EdgeSpec {
                from: String::from(from),
                to: String::from(to),
                timeout: OFFER_TIMEOUT,
            };
            FlowSpec {
                producers: vec![ProducerSpec {
                    name: self.producer_name.clone(),
                    tokens: ONE_TOKEN,
                }],
                engines: vec![
                    EngineSpec {
                        name: String::from(filter),
                        location: Location::Local,
                    },
                    EngineSpec {
                        name: String::from(detector),
                        location: Location::Remote,
                    },
                ],
                edges: vec![edge(&self.producer_name, filter), edge(filter, detector)],
            }
        }
    }

    /// Starts the engine `name`, and serves it on `server`.
    fn serve(server: &mut Server, name: &str) -> Engine {
        let engine = Engine::new(name, None, MAX_OUTPUT_SIZE).unwrap();
        server.add_engine(name).unwrap();
        engine
    }

    #[test]
    fn input_a_local_filter_passes_is_processed_remotely() {
        const RESULT: &[u8] = &[4, 2];
        let mut fixture = Offload::new();
        let (filter_name, detector_name) = (unique("filter"), unique("detector"));
        let mut filter = serve(&mut fixture.local, &filter_name);
        let mut detector = serve(&mut fixture.remote, &detector_name);
        let mut subscription = Subscription::producer(&fixture.producer_name).unwrap();
        // The remote part is installed once the client connects, which may be after this.
        let spec = fixture.spec(&filter_name, &detector_name);
        fixture.client.add_flow(FLOW, &spec).unwrap();

        publish(&mut fixture.producer, 7);
        assert_eq!(next_input(&mut filter), 7);
        filter.pass(&[]).unwrap();
        assert_eq!(next_input(&mut detector), 7);
        detector.pass(RESULT).unwrap();

        let mut seen = Vec::new();
        while !seen
            .iter()
            .any(|(kind, engine, _, _)| *kind == EventKind::Result && *engine == detector_name)
        {
            seen.push(
                try_next(&mut subscription, WAIT, |event| {
                    (
                        event.kind(),
                        event.engine().to_owned(),
                        event.received_at().is_some(),
                        event.result().to_vec(),
                    )
                })
                .expect("no event arrived"),
            );
        }
        assert_eq!(
            seen,
            vec![
                (EventKind::Sent, filter_name.clone(), false, vec![]),
                (EventKind::Result, filter_name, false, vec![]),
                (EventKind::Sent, detector_name.clone(), true, vec![]),
                (EventKind::Result, detector_name, true, RESULT.to_vec()),
            ]
        );
    }

    #[test]
    fn lost_remote_server_is_reported() {
        let mut fixture = Offload::new();
        let (filter_name, detector_name) = (unique("filter"), unique("detector"));
        let mut filter = serve(&mut fixture.local, &filter_name);
        let mut detector = serve(&mut fixture.remote, &detector_name);
        let mut subscription = Subscription::producer(&fixture.producer_name).unwrap();
        let spec = fixture.spec(&filter_name, &detector_name);
        fixture.client.add_flow(FLOW, &spec).unwrap();
        // One input through both servers, so the remote part is installed.
        publish(&mut fixture.producer, 7);
        next_input(&mut filter);
        filter.pass(&[]).unwrap();
        next_input(&mut detector);
        detector.pass(&[]).unwrap();

        let Offload { remote, .. } = fixture;
        drop(remote);

        let disconnected = (0..)
            .map(|_| next_event(&mut subscription))
            .find(|(kind, ..)| *kind == EventKind::Disconnected);
        assert!(disconnected.is_some());
    }

    #[test]
    fn flow_with_remote_engines_needs_a_remote_server() {
        let mut fixture = Fixture::new();
        let filter = unique("filter");
        fixture.engine(&filter);
        let mut spec = fixture.chain(ONE_TOKEN, &[&filter, "detector"]);
        spec.engines[1].location = Location::Remote;

        let added = fixture.client.add_flow(FLOW, &spec);

        assert!(matches!(added, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn remote_crossings_need_capacity() {
        let remote = RemoteServerConfig {
            crossing_capacity: 0,
            ..RemoteServerConfig::new(std::net::SocketAddr::from(([127, 0, 0, 1], 0)), Vec::new())
        };

        let created = Client::new(&unique("client"), &unique("server"), Some(remote));

        assert!(matches!(created, Err(Error::InvalidArgument(_))));
    }

    #[test]
    fn client_is_send() {
        fn assert_send<T: Send>() {}
        assert_send::<Client>();
    }
}
