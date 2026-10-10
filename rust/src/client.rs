//! Client: takes inputs from local producers, and routes them through the flows it added. Each
//! flow's engines run on the local server; the remote server is not supported yet. Its work runs
//! on its own thread.
mod compile;
mod local_server;
mod receiver;
mod worker;

use std::{sync::mpsc, thread::JoinHandle};

use crate::{Error, Result, client::worker::Command, spec::FlowSpec};

/// Bridges local producers to the servers that run a flow's engines.
#[derive(Debug)]
pub struct Client {
    name: String,
    local_server: String,
    remote_server_addr: String,
    commands: Option<mpsc::Sender<Command>>,
    thread: Option<JoinHandle<Result<()>>>,
}

impl Client {
    /// Creates a client named `name` that talks to the local server `local_server` and the remote
    /// server at the network address `remote_server_addr`. The local server may start later.
    pub fn new(name: &str, local_server: &str, remote_server_addr: &str) -> Result<Self> {
        let (commands, thread) = worker::spawn(name, local_server)?;
        Ok(Self {
            name: String::from(name),
            local_server: String::from(local_server),
            remote_server_addr: String::from(remote_server_addr),
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

    /// Adds the flow `name` described by `spec`: installs its engines' part on the local server,
    /// and routes the inputs its producers publish on `name`. Its producers must be added first.
    pub fn add_flow(&mut self, name: &str, spec: &FlowSpec) -> Result<()> {
        self.request(|reply| Command::AddFlow {
            name: name.to_string(),
            spec: spec.clone(),
            reply,
        })
    }

    /// Removes the flow `name` from the client and from the local server.
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
    use std::time::Duration;

    use crate::{
        Client, Engine, Error, Producer, Server,
        spec::{EdgeSpec, EngineSpec, FlowSpec, Location, ProducerSpec},
        test_util::{FLOW, INPUT_LEN, MAX_INPUT_SIZE, publish, unique},
    };

    const REMOTE_SERVER_ADDR: &str = "127.0.0.1:4433";
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
        client: Client,
        producer: Producer,
        producer_name: String,
    }

    impl Fixture {
        fn new() -> Self {
            let server_name = unique("server");
            let server = Server::new(&server_name, None).unwrap();
            let mut client =
                Client::new(&unique("client"), &server_name, REMOTE_SERVER_ADDR).unwrap();
            let producer_name = unique("camera");
            let producer = Producer::new(&producer_name, MAX_INPUT_SIZE).unwrap();
            client.add_producer(&producer_name).unwrap();
            Self {
                server,
                client,
                producer,
                producer_name,
            }
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
        let mut client =
            Client::new(&unique("client"), &unique("server"), REMOTE_SERVER_ADDR).unwrap();
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
        let mut client =
            Client::new(&unique("client"), &unique("server"), REMOTE_SERVER_ADDR).unwrap();
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

    #[test]
    fn client_is_send() {
        fn assert_send<T: Send>() {}
        assert_send::<Client>();
    }
}
