//! What the client and the remote server share about their QUIC connection: its settings, its
//! TLS configuration, and the thread that runs it.
//!
//! quinn runs on tokio, but the client and server threads block in iceoryx2 waitsets. So each
//! process that speaks QUIC runs a network thread with its own single-threaded runtime. It talks
//! to the client or server thread over channels, and wakes it through an iceoryx2 event, which
//! that thread's waitset listens to.

use std::{
    sync::{Arc, mpsc},
    thread::{self, JoinHandle},
    time::Duration,
};

use iceoryx2::{port::notifier::Notifier, prelude::*};
use quinn::{
    ClientConfig, IdleTimeout, ServerConfig, TransportConfig, rustls,
    rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer},
};
use tokio_util::codec::LengthDelimitedCodec;

use crate::{Error, Result, services};

/// The name the remote server's certificate is made for, and the client checks.
pub(crate) const SERVER_NAME: &str = "gabriel";
/// How long a connection may carry no packet before it counts as lost. It sets how soon a loss is
/// noticed, against how long a link may stall.
const IDLE_TIMEOUT: Duration = Duration::from_secs(5);
/// How often a quiet connection sends a packet, so that a working link does not time out.
const KEEP_ALIVE_INTERVAL: Duration = Duration::from_secs(1);
/// The largest control call or reply.
pub(crate) const MAX_CONTROL_SIZE: usize = 1 << 20;
/// The largest input, with its header.
pub(crate) const MAX_INPUT_SIZE: usize = 64 << 20;
/// The largest event, with its result.
const MAX_EVENT_SIZE: usize = 64 << 20;
/// How long a network thread waits for its connections to close when it stops.
pub(crate) const CLOSE_TIMEOUT: Duration = Duration::from_secs(1);

/// The QUIC error code of a connection closed on purpose.
pub(crate) const CLOSED: quinn::VarInt = quinn::VarInt::from_u32(0);
/// The QUIC error code of a connection closed because its peer broke the protocol, or could not
/// keep up.
pub(crate) const BROKEN: quinn::VarInt = quinn::VarInt::from_u32(1);

/// The settings both ends use.
fn transport() -> Result<Arc<TransportConfig>> {
    let mut transport = TransportConfig::default();
    transport
        .max_idle_timeout(Some(IdleTimeout::try_from(IDLE_TIMEOUT).map_err(|e| {
            Error::Internal(format!("invalid idle timeout {IDLE_TIMEOUT:?}: {e}"))
        })?))
        .keep_alive_interval(Some(KEEP_ALIVE_INTERVAL));
    Ok(Arc::new(transport))
}

/// A new self-signed certificate for [`SERVER_NAME`], and the configuration of a server that
/// presents it.
pub(crate) fn server_config() -> Result<(ServerConfig, CertificateDer<'static>)> {
    let certified = rcgen::generate_simple_self_signed(vec![String::from(SERVER_NAME)])
        .map_err(|e| Error::Internal(format!("failed to make a certificate: {e}")))?;
    let certificate = certified.cert.der().clone();
    let key = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(
        certified.signing_key.serialize_der(),
    ));
    let mut config = ServerConfig::with_single_cert(vec![certificate.clone()], key)
        .map_err(|e| Error::Internal(format!("invalid certificate: {e}")))?;
    config.transport_config(transport()?);
    Ok((config, certificate))
}

/// The configuration of a client that trusts only `certificate`.
pub(crate) fn client_config(certificate: &[u8]) -> Result<ClientConfig> {
    let mut roots = rustls::RootCertStore::empty();
    roots
        .add(CertificateDer::from(certificate.to_vec()))
        .map_err(|e| Error::InvalidArgument(format!("invalid server certificate: {e}")))?;
    let mut config = ClientConfig::with_root_certificates(Arc::new(roots))
        .map_err(|e| Error::Internal(format!("failed to configure TLS: {e}")))?;
    config.transport_config(transport()?);
    Ok(config)
}

/// Frames the long-lived stream of events, one length-prefixed message per event.
pub(crate) fn event_codec() -> LengthDelimitedCodec {
    LengthDelimitedCodec::builder()
        .max_frame_length(MAX_EVENT_SIZE)
        .new_codec()
}

/// An error of the network, about `what`.
pub(crate) fn error(what: &str, e: impl std::fmt::Display) -> Error {
    Error::Internal(format!("{what}: {e}"))
}

/// Wakes the thread that reads what the network thread received.
pub(crate) struct Waker(Notifier<ipc::Service>);

impl Waker {
    pub(crate) fn wake(&self) {
        // A wake-up that fails is not lost for long: the woken thread also wakes on a timer.
        if let Err(e) = self.0.notify() {
            tracing::warn!(error = %e, "failed to wake a thread");
        }
    }
}

/// Starts the network thread `name`. In it, `setup` runs with the waker of the event `ready`;
/// what it returns is sent back once it succeeded, and the future it makes runs to its end.
pub(crate) fn spawn<T, F>(
    name: String,
    ready: ServiceName,
    setup: impl FnOnce(Waker) -> Result<(T, F)> + Send + 'static,
) -> Result<(T, JoinHandle<()>)>
where
    T: Send + 'static,
    F: Future<Output = ()>,
{
    let (started_tx, started_rx) = mpsc::channel();
    let thread = thread::Builder::new()
        .name(name.clone())
        .spawn(move || {
            let runtime = tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .map_err(|e| error("failed to start the network runtime", e));
            // The endpoint must be made inside the runtime, and the notifier in this thread.
            let started = runtime.and_then(|runtime| {
                let _entered = runtime.enter();
                let node = NodeBuilder::new()
                    .create::<ipc::Service>()
                    .map_err(|e| error("failed to create node", e))?;
                let notifier = services::open_remote_ready(&node, &ready)?
                    .notifier_builder()
                    .create()
                    .map_err(|e| error("failed to create notifier", e))?;
                let (value, future) = setup(Waker(notifier))?;
                Ok((runtime, node, value, future))
            });
            match started {
                Ok((runtime, _node, value, future)) => {
                    let _ = started_tx.send(Ok(value));
                    runtime.block_on(future);
                }
                Err(e) => {
                    let _ = started_tx.send(Err(e));
                }
            }
        })
        .map_err(|e| error("failed to spawn the network thread", e))?;
    match started_rx.recv() {
        Ok(Ok(value)) => Ok((value, thread)),
        Ok(Err(e)) => {
            let _ = thread.join();
            Err(e)
        }
        Err(_) => {
            let _ = thread.join();
            Err(Error::Internal(format!("{name} stopped during startup")))
        }
    }
}
