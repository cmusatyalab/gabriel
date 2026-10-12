//! What the client and the remote server share about their QUIC connection: its settings, its
//! TLS configuration, and the thread that runs it.
//!
//! quinn runs on tokio, but the client and server threads block in iceoryx2 waitsets. So each
//! process that speaks QUIC runs a network thread with its own single-threaded runtime. It talks
//! to the client or server thread over channels, and wakes it through an iceoryx2 event, which
//! that thread's waitset listens to.

use std::{
    fs,
    io::{self, Write},
    os::unix::fs::{OpenOptionsExt, PermissionsExt},
    path::Path,
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

/// The file in a server's identity directory that holds its certificate, in DER.
const CERTIFICATE_FILE: &str = "certificate.der";
/// The file in a server's identity directory that holds its private key, in DER.
const KEY_FILE: &str = "key.der";
/// Anyone may read the certificate; it is what clients are given.
const CERTIFICATE_MODE: u32 = 0o644;
/// Only the owner may read the key.
const KEY_MODE: u32 = 0o600;

/// What a remote server proves itself with: a self-signed certificate for [`SERVER_NAME`], and
/// its private key.
pub(crate) struct Identity {
    certificate: CertificateDer<'static>,
    key: PrivateKeyDer<'static>,
}

impl Identity {
    /// A new identity, which lasts as long as the server.
    pub(crate) fn generate() -> Result<Self> {
        let certified = rcgen::generate_simple_self_signed(vec![String::from(SERVER_NAME)])
            .map_err(|e| Error::Internal(format!("failed to make a certificate: {e}")))?;
        Ok(Self {
            certificate: certified.cert.der().clone(),
            key: PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(
                certified.signing_key.serialize_der(),
            )),
        })
    }

    /// The identity saved in the directory `dir`, which must exist. The first time, when `dir`
    /// holds none, a new one is made and saved there, so that the server keeps its certificate
    /// across restarts, and its clients can still trust it.
    pub(crate) fn load_or_generate(dir: &Path) -> Result<Self> {
        let certificate_path = dir.join(CERTIFICATE_FILE);
        let key_path = dir.join(KEY_FILE);
        match (
            read_if_exists(&certificate_path)?,
            read_if_exists(&key_path)?,
        ) {
            (Some(certificate), Some(key)) => Ok(Self {
                certificate: CertificateDer::from(certificate),
                key: PrivateKeyDer::try_from(key).map_err(|e| {
                    Error::InvalidArgument(format!("invalid key in {}: {e}", key_path.display()))
                })?,
            }),
            (None, None) => {
                let identity = Self::generate()?;
                // The key first: a certificate without its key would be of no use.
                write_new(&key_path, identity.key.secret_der(), KEY_MODE)?;
                write_new(&certificate_path, &identity.certificate, CERTIFICATE_MODE)?;
                Ok(identity)
            }
            _ => Err(Error::InvalidArgument(format!(
                "{} must hold both {CERTIFICATE_FILE} and {KEY_FILE}, or neither",
                dir.display()
            ))),
        }
    }

    /// The certificate clients must trust, in DER.
    pub(crate) fn certificate(&self) -> &[u8] {
        &self.certificate
    }

    /// The configuration of a server that presents this identity.
    pub(crate) fn server_config(self) -> Result<ServerConfig> {
        let mut config = ServerConfig::with_single_cert(vec![self.certificate], self.key)
            .map_err(|e| Error::InvalidArgument(format!("invalid certificate or key: {e}")))?;
        config.transport_config(transport()?);
        Ok(config)
    }
}

/// The contents of the file at `path`, or `None` if there is none.
fn read_if_exists(path: &Path) -> Result<Option<Vec<u8>>> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(file_error(path, e)),
    }
}

/// Writes `bytes` to a new file at `path`, with the permissions `mode`.
fn write_new(path: &Path, bytes: &[u8], mode: u32) -> Result<()> {
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .open(path)
        .and_then(|mut file| {
            // Set again, because the umask may have taken bits off: iceoryx2 changes it for the
            // whole process while it binds a socket.
            file.set_permissions(fs::Permissions::from_mode(mode))?;
            file.write_all(bytes)
        })
        .map_err(|e| file_error(path, e))
}

fn file_error(path: &Path, e: io::Error) -> Error {
    Error::InvalidArgument(format!("{}: {e}", path.display()))
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
