//! C ABI. Every function returns a [`Status`]; handles are opaque pointers
//! created by `*_create` and freed by `*_destroy`, and a function that acts on a handle takes it
//! first. After a failure, [`gabriel_last_error`] says what went wrong.
//!
//! The ABI has a version, [`ABI_VERSION`], which changes whenever a call or a struct changes in a
//! way that breaks code built against the previous version. A binding that loads the library at
//! run time compares the header's version with [`gabriel_abi_version`]. Enums are 32 bits wide,
//! and new variants may be added at the end, so code should handle values it does not know.
//!
//! A producer, an engine and a subscription must stay on the thread that created them: create,
//! use and destroy each on one thread. A client and a server may be used from any thread.

use std::{
    cell::RefCell,
    ffi::{CStr, CString, c_char},
    path::PathBuf,
    ptr,
    time::Duration,
};

use iceoryx2_bb_container::string::String as _;

use crate::{
    Client, Engine, Error, EventKind, FlowSpec, Forward, Producer, RemoteServerConfig, Result,
    Server, Subscription, logging, subscription::Event,
};

/// The version of the ABI that this library implements.
pub const ABI_VERSION: u32 = 1;

/// The version of the ABI that the library implements. A caller built with a header of another
/// version (`GABRIEL_ABI_VERSION`) must not use the library.
#[unsafe(no_mangle)]
pub extern "C" fn gabriel_abi_version() -> u32 {
    ABI_VERSION
}

/// Status code returned by every C ABI function.
#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok = 0,
    ErrInvalid,
    ErrSizeExceeded,
    ErrAgain,
    ErrInternal,
}

thread_local! {
    /// The message of the last failure on this thread.
    static LAST_ERROR: RefCell<CString> = RefCell::default();
}

/// Records `err` as this thread's last error, for [`gabriel_last_error`].
fn set_last_error(err: &Error) {
    // A message with a NUL in it is cut there, rather than lost.
    let message = err.to_string();
    let message = CString::new(message).unwrap_or_else(|e| {
        let nul = e.nul_position();
        let mut bytes = e.into_vec();
        bytes.truncate(nul);
        CString::new(bytes).expect("cut before the first NUL")
    });
    LAST_ERROR.with(|last| *last.borrow_mut() = message);
}

/// Says what went wrong in the last call on this thread that failed. The string stays valid until
/// the next call on this thread that fails. It is "" if no call has failed yet.
#[unsafe(no_mangle)]
pub extern "C" fn gabriel_last_error() -> *const c_char {
    LAST_ERROR.with(|last| last.borrow().as_ptr())
}

impl From<&Error> for Status {
    fn from(err: &Error) -> Self {
        set_last_error(err);
        match err {
            Error::InvalidArgument(_) => Status::ErrInvalid,
            Error::SizeExceeded { .. } => Status::ErrSizeExceeded,
            Error::Again => Status::ErrAgain,
            Error::Internal(_) => Status::ErrInternal,
        }
    }
}

impl From<Result<()>> for Status {
    fn from(result: Result<()>) -> Self {
        match result {
            Ok(()) => Status::Ok,
            Err(err) => Status::from(&err),
        }
    }
}

/// Borrows a C string as `&str`.
///
/// # Safety
/// `ptr` must be null or point to a NUL-terminated string that outlives `'a`.
unsafe fn str_arg<'a>(ptr: *const c_char) -> Result<&'a str> {
    if ptr.is_null() {
        return Err(Error::InvalidArgument("null string".into()));
    }
    // SAFETY: non-null and NUL-terminated per the caller's contract.
    unsafe { CStr::from_ptr(ptr) }
        .to_str()
        .map_err(|e| Error::InvalidArgument(format!("string is not valid UTF-8: {e}")))
}

/// Like [`str_arg`], but null means no string.
///
/// # Safety
/// As for [`str_arg`].
unsafe fn opt_str_arg<'a>(ptr: *const c_char) -> Result<Option<&'a str>> {
    if ptr.is_null() {
        return Ok(None);
    }
    // SAFETY: non-null, and valid per the caller's contract.
    unsafe { str_arg(ptr) }.map(Some)
}

/// Borrows an opaque handle mutably.
///
/// # Safety
/// `ptr` must be null or a live handle returned by the matching `*_create`.
unsafe fn handle_arg<'a, T>(ptr: *mut T) -> Result<&'a mut T> {
    // SAFETY: valid or null per the caller's contract.
    unsafe { ptr.as_mut() }.ok_or_else(|| Error::InvalidArgument("null handle".into()))
}

/// Stores a newly created object in `*out` as an owned, opaque handle.
///
/// # Safety
/// `out` must be null or valid for writes.
unsafe fn create_handle<T>(out: *mut *mut T, create: impl FnOnce() -> Result<T>) -> Status {
    if out.is_null() {
        return null_argument("output pointer");
    }
    let result = create().map(|value| {
        // SAFETY: `out` is non-null and writable per the caller's contract.
        unsafe { *out = Box::into_raw(Box::new(value)) };
    });
    Status::from(result)
}

/// Fails a call that was given a null `name`.
fn null_argument(name: &str) -> Status {
    Status::from(&Error::InvalidArgument(format!("null {name}")))
}

/// Frees a handle returned by the matching `*_create`. Null is a no-op.
///
/// # Safety
/// `ptr` must be null or a live handle that is not used afterwards.
unsafe fn destroy_handle<T>(ptr: *mut T) -> Status {
    if !ptr.is_null() {
        // SAFETY: `ptr` came from `Box::into_raw` in `create_handle`.
        drop(unsafe { Box::from_raw(ptr) });
    }
    Status::Ok
}

// --- Logging ---

/// Prints Gabriel's log to standard error. `filter` picks what is printed, in the syntax of
/// `RUST_LOG`, e.g. "info" or "gabriel=debug". If it is NULL, `RUST_LOG` picks, or warnings and
/// errors are printed if that is unset. Call it once, before anything else; without it, nothing is
/// logged. Fails with `ERR_INVALID` for a malformed filter, or when called a second time.
///
/// # Safety
/// `filter` must be NUL-terminated or null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_log_init(filter: *const c_char) -> Status {
    Status::from(unsafe { opt_str_arg(filter) }.and_then(logging::init))
}

// --- Producer ---

/// Creates a producer. Use and destroy it only on the thread that created it.
///
/// # Safety
/// String arguments must be NUL-terminated; `producer` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_producer_create(
    name: *const c_char,
    max_input_size: usize,
    producer: *mut *mut Producer,
) -> Status {
    unsafe { create_handle(producer, || Producer::new(str_arg(name)?, max_input_size)) }
}

/// # Safety
/// `producer` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_producer_destroy(producer: *mut Producer) -> Status {
    unsafe { destroy_handle(producer) }
}

/// # Safety
/// `producer` must be a live handle; `buf` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_producer_new_input(
    producer: *mut Producer,
    buf: *mut *mut u8,
) -> Status {
    if buf.is_null() {
        return null_argument("buf");
    }
    let result = unsafe { handle_arg(producer) }
        .and_then(Producer::new_input)
        .map(|input| unsafe { *buf = input.as_mut_ptr().cast() });
    Status::from(result)
}

/// Publishes the first `len` bytes of the input on `flow`. If `seq` is not null, it gets the
/// input's sequence number, which the events about the input carry.
///
/// # Safety
/// `producer` must be a live handle; `flow` must be NUL-terminated; `seq` must be null or valid
/// for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_producer_publish_input(
    producer: *mut Producer,
    len: usize,
    flow: *const c_char,
    seq: *mut u64,
) -> Status {
    let result = unsafe { (|| handle_arg(producer)?.publish_input(len, str_arg(flow)?))() };
    Status::from(result.map(|id| {
        if !seq.is_null() {
            unsafe { seq.write(id.seq()) };
        }
    }))
}

// --- Client ---

/// How to create a client, beyond its name and local server. New options are added as new
/// setters, so `gabriel_client_create` keeps its signature.
#[derive(Debug, Default)]
pub struct ClientOptions {
    remote: Option<RemoteServerConfig>,
    remote_crossing_capacity: Option<usize>,
}

/// Creates client options with the defaults: no remote server.
///
/// # Safety
/// `options` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_options_create(options: *mut *mut ClientOptions) -> Status {
    unsafe { create_handle(options, || Ok(ClientOptions::default())) }
}

/// # Safety
/// `options` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_options_destroy(options: *mut ClientOptions) -> Status {
    unsafe { destroy_handle(options) }
}

/// Connects the client to the remote server at `addr`, e.g. "192.0.2.1:4433". The client trusts
/// only the certificate of `certificate_len` bytes at `certificate`, in DER, as
/// `gabriel_server_certificate` gives it.
///
/// # Safety
/// `options` must be a live handle; `addr` must be NUL-terminated; `certificate` must point to
/// `certificate_len` readable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_options_set_remote_server(
    options: *mut ClientOptions,
    addr: *const c_char,
    certificate: *const u8,
    certificate_len: usize,
) -> Status {
    let result = unsafe {
        (|| {
            let options = handle_arg(options)?;
            let addr = str_arg(addr)?;
            let addr = addr
                .parse()
                .map_err(|e| Error::InvalidArgument(format!("invalid address {addr:?}: {e}")))?;
            let certificate = bytes_arg(certificate, certificate_len)?.to_vec();
            options.remote = Some(RemoteServerConfig::new(addr, certificate));
            Ok(())
        })()
    };
    Status::from(result)
}

/// Sets how many inputs each crossing to the remote server may have in flight: on the network,
/// or waiting at the server. It must be at least 1, and needs a remote server. Without it,
/// crossings get the default capacity.
///
/// # Safety
/// `options` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_options_set_remote_crossing_capacity(
    options: *mut ClientOptions,
    capacity: usize,
) -> Status {
    let result = unsafe { handle_arg(options) }.map(|options| {
        options.remote_crossing_capacity = Some(capacity);
    });
    Status::from(result)
}

/// Creates a client of the local server `local_server`. `options` may be NULL for the defaults;
/// the client copies what it needs, so they may be destroyed right after.
///
/// # Safety
/// String arguments must be NUL-terminated; `options` must be null or a live handle; `client`
/// must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_create(
    name: *const c_char,
    local_server: *const c_char,
    options: *const ClientOptions,
    client: *mut *mut Client,
) -> Status {
    unsafe {
        create_handle(client, || {
            let options = options.as_ref();
            let mut remote = options.and_then(|options| options.remote.clone());
            if let Some(capacity) = options.and_then(|options| options.remote_crossing_capacity) {
                let remote = remote.as_mut().ok_or_else(|| {
                    Error::InvalidArgument(String::from(
                        "a remote crossing capacity needs a remote server",
                    ))
                })?;
                remote.crossing_capacity = capacity;
            }
            Client::new(str_arg(name)?, str_arg(local_server)?, remote)
        })
    }
}

/// # Safety
/// `client` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_destroy(client: *mut Client) -> Status {
    unsafe { destroy_handle(client) }
}

/// # Safety
/// `client` must be a live handle; `name` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_add_producer(
    client: *mut Client,
    name: *const c_char,
) -> Status {
    Status::from(unsafe { (|| handle_arg(client)?.add_producer(str_arg(name)?))() })
}

/// # Safety
/// `client` must be a live handle; `name` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_remove_producer(
    client: *mut Client,
    name: *const c_char,
) -> Status {
    Status::from(unsafe { (|| handle_arg(client)?.remove_producer(str_arg(name)?))() })
}

/// Adds the flow `name`. `spec` describes it in TOML, as a list of edges; see the `spec` module
/// of the Rust API for an example.
///
/// # Safety
/// `client` must be a live handle; string arguments must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_add_flow(
    client: *mut Client,
    name: *const c_char,
    spec: *const c_char,
) -> Status {
    Status::from(unsafe {
        (|| {
            let spec: FlowSpec = str_arg(spec)?.parse()?;
            handle_arg(client)?.add_flow(str_arg(name)?, &spec)
        })()
    })
}

/// # Safety
/// `client` must be a live handle; `name` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_remove_flow(
    client: *mut Client,
    name: *const c_char,
) -> Status {
    Status::from(unsafe { (|| handle_arg(client)?.remove_flow(str_arg(name)?))() })
}

// --- Server ---

/// How to create a server, beyond its name. New options are added as new setters, so
/// `gabriel_server_create` keeps its signature.
#[derive(Debug, Default)]
pub struct ServerOptions {
    bind_addr: Option<String>,
    identity_dir: Option<PathBuf>,
}

/// Creates server options with the defaults: a server with local clients only.
///
/// # Safety
/// `options` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_options_create(options: *mut *mut ServerOptions) -> Status {
    unsafe { create_handle(options, || Ok(ServerOptions::default())) }
}

/// # Safety
/// `options` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_options_destroy(options: *mut ServerOptions) -> Status {
    unsafe { destroy_handle(options) }
}

/// Makes the server accept remote clients on the network address `addr`, e.g. "0.0.0.0:4433".
///
/// # Safety
/// `options` must be a live handle; `addr` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_options_set_bind_addr(
    options: *mut ServerOptions,
    addr: *const c_char,
) -> Status {
    let result = unsafe {
        (|| {
            handle_arg(options)?.bind_addr = Some(str_arg(addr)?.to_owned());
            Ok(())
        })()
    };
    Status::from(result)
}

/// Makes a server that accepts remote clients keep its certificate and key in `dir`, an existing
/// directory, so that they stay the same across restarts; the server makes them there the first
/// time. Without it, the server makes a new certificate each time it starts. It needs a bind
/// address.
///
/// # Safety
/// `options` must be a live handle; `dir` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_options_set_identity_dir(
    options: *mut ServerOptions,
    dir: *const c_char,
) -> Status {
    let result = unsafe {
        (|| {
            handle_arg(options)?.identity_dir = Some(PathBuf::from(str_arg(dir)?));
            Ok(())
        })()
    };
    Status::from(result)
}

/// Creates the server `name`. `options` may be NULL for the defaults; the server copies what it
/// needs, so they may be destroyed right after.
///
/// # Safety
/// `name` must be NUL-terminated; `options` must be null or a live handle; `server` must be valid
/// for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_create(
    name: *const c_char,
    options: *const ServerOptions,
    server: *mut *mut Server,
) -> Status {
    unsafe {
        create_handle(server, || {
            let options = options.as_ref();
            Server::new(
                str_arg(name)?,
                options.and_then(|options| options.bind_addr.as_deref()),
                options.and_then(|options| options.identity_dir.as_deref()),
            )
        })
    }
}

/// # Safety
/// `server` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_destroy(server: *mut Server) -> Status {
    unsafe { destroy_handle(server) }
}

/// Writes the port remote clients reach the server on to `*port`. Fails for a server created
/// without a bind address.
///
/// # Safety
/// `server` must be a live handle; `port` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_remote_port(server: *mut Server, port: *mut u16) -> Status {
    let result = unsafe {
        (|| {
            let addr = handle_arg(server)?.remote_addr().ok_or_else(not_remote)?;
            let port = port
                .as_mut()
                .ok_or_else(|| Error::InvalidArgument("null port".into()))?;
            *port = addr.port();
            Ok(())
        })()
    };
    Status::from(result)
}

/// Points `*certificate` to the certificate remote clients must trust, in DER, and writes its
/// length to `*len`. The bytes stay valid while the server lives. Fails for a server created
/// without a bind address.
///
/// # Safety
/// `server` must be a live handle; `certificate` and `len` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_certificate(
    server: *mut Server,
    certificate: *mut *const u8,
    len: *mut usize,
) -> Status {
    let result = unsafe {
        (|| {
            let bytes = handle_arg(server)?.certificate().ok_or_else(not_remote)?;
            let (Some(certificate), Some(len)) = (certificate.as_mut(), len.as_mut()) else {
                return Err(Error::InvalidArgument("null output".into()));
            };
            *certificate = bytes.as_ptr();
            *len = bytes.len();
            Ok(())
        })()
    };
    Status::from(result)
}

fn not_remote() -> Error {
    Error::InvalidArgument("the server accepts no remote clients".into())
}

/// # Safety
/// `server` must be a live handle; `name` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_add_engine(
    server: *mut Server,
    name: *const c_char,
) -> Status {
    Status::from(unsafe { (|| handle_arg(server)?.add_engine(str_arg(name)?))() })
}

/// # Safety
/// `server` must be a live handle; `name` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_remove_engine(
    server: *mut Server,
    name: *const c_char,
) -> Status {
    Status::from(unsafe { (|| handle_arg(server)?.remove_engine(str_arg(name)?))() })
}

// --- Engine ---

/// Identifies one input of a producer. `producer` stays valid until the engine reports its
/// outcome.
#[repr(C)]
pub struct InputIdView {
    producer: *const c_char,
    session: u64,
    seq: u64,
}

/// An input handed to an engine. Stays valid until the engine reports its outcome.
#[repr(C)]
pub struct InputView {
    id: InputIdView,
    /// How to read `buf`; "" if the input has no convention.
    payload_type: *const c_char,
    buf: *const u8,
    len: usize,
}

/// Borrows `len` bytes at `ptr`; `ptr` may be null when `len` is 0.
///
/// # Safety
/// `ptr` must be null or point to `len` readable bytes that outlive `'a`.
unsafe fn bytes_arg<'a>(ptr: *const u8, len: usize) -> Result<&'a [u8]> {
    if len == 0 {
        return Ok(&[]);
    }
    if ptr.is_null() {
        return Err(Error::InvalidArgument("null buffer".into()));
    }
    // SAFETY: non-null and `len` readable bytes per the caller's contract.
    Ok(unsafe { std::slice::from_raw_parts(ptr, len) })
}

/// Creates an engine. Use and destroy it only on the thread that created it. `result_type` names
/// how to read this engine's results, e.g. a protobuf message name. It may be NULL.
///
/// # Safety
/// String arguments must be NUL-terminated (`result_type` may be null); `engine` must be valid
/// for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_create(
    name: *const c_char,
    result_type: *const c_char,
    max_output_size: usize,
    engine: *mut *mut Engine,
) -> Status {
    unsafe {
        create_handle(engine, || {
            Engine::new(str_arg(name)?, opt_str_arg(result_type)?, max_output_size)
        })
    }
}

/// # Safety
/// `engine` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_destroy(engine: *mut Engine) -> Status {
    unsafe { destroy_handle(engine) }
}

/// Waits until an input or an event of a subscription is ready, or `timeout_ms` passes, and
/// returns `ERR_AGAIN` then. Fetch the input with `gabriel_engine_poll_input`, and the event with
/// `gabriel_engine_poll_event`; either may still return `ERR_AGAIN`.
///
/// # Safety
/// `engine` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_wait(engine: *mut Engine, timeout_ms: u32) -> Status {
    let timeout = Duration::from_millis(u64::from(timeout_ms));
    Status::from(unsafe { handle_arg(engine) }.and_then(|engine| engine.wait(timeout)))
}

/// Returns `ERR_AGAIN` if no input is available. The input stays valid until the engine reports
/// its outcome.
///
/// # Safety
/// `engine` must be a live handle; `input` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_poll_input(
    engine: *mut Engine,
    input: *mut InputView,
) -> Status {
    if input.is_null() {
        return null_argument("input");
    }
    let result = unsafe { handle_arg(engine) }
        .and_then(Engine::poll_input)
        .map(|polled| {
            let view = InputView {
                id: InputIdView {
                    producer: polled.id.producer.as_c_str(),
                    session: polled.id.session,
                    seq: polled.id.seq,
                },
                payload_type: polled.payload_type.as_c_str(),
                buf: polled.data.as_ptr(),
                len: polled.data.len(),
            };
            // SAFETY: `input` is non-null and writable per the caller's contract.
            unsafe { input.write(view) };
        });
    Status::from(result)
}

/// Subscribes the engine to the events of the engine `name`, e.g. a detector whose results it
/// fuses. Only events published from now on are read. `gabriel_engine_wait` ends for them too.
///
/// # Safety
/// `engine` must be a live handle; `name` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_subscribe(
    engine: *mut Engine,
    name: *const c_char,
) -> Status {
    let result = unsafe { handle_arg(engine) }
        .and_then(|engine| engine.subscribe(unsafe { str_arg(name) }?));
    Status::from(result)
}

/// Returns `ERR_AGAIN` if no event of the engine's subscriptions is available. The event stays
/// valid until the next poll. Its `engine` says which subscription it came from.
///
/// # Safety
/// `engine` must be a live handle; `event` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_poll_event(
    engine: *mut Engine,
    event: *mut EventView,
) -> Status {
    if event.is_null() {
        return null_argument("event");
    }
    let result = unsafe { handle_arg(engine) }
        .and_then(Engine::poll_event)
        // SAFETY: `event` is non-null and writable per the caller's contract.
        .map(|polled| unsafe { event.write(EventView::new(&polled)) });
    Status::from(result)
}

/// Stop: forward nothing. A discard is a stop with no result (`result` null, `result_len` 0).
///
/// # Safety
/// `engine` must be a live handle; `result` must point to `result_len` readable bytes, or be
/// null when `result_len` is 0.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_stop(
    engine: *mut Engine,
    result: *const u8,
    result_len: usize,
) -> Status {
    Status::from(unsafe { (|| handle_arg(engine)?.stop(bytes_arg(result, result_len)?))() })
}

/// Pass: forward the input unchanged along every outgoing input edge.
///
/// # Safety
/// As for [`gabriel_engine_stop`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_pass(
    engine: *mut Engine,
    result: *const u8,
    result_len: usize,
) -> Status {
    Status::from(unsafe { (|| handle_arg(engine)?.pass(bytes_arg(result, result_len)?))() })
}

/// Loans a buffer for a new input. Fill it, then call `gabriel_engine_emit`.
///
/// # Safety
/// `engine` must be a live handle; `buf` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_new_output(
    engine: *mut Engine,
    buf: *mut *mut u8,
) -> Status {
    if buf.is_null() {
        return null_argument("buf");
    }
    let result = unsafe { handle_arg(engine) }
        .and_then(Engine::new_output)
        // SAFETY: `buf` is non-null and writable per the caller's contract.
        .map(|output| unsafe { *buf = output.as_mut_ptr().cast() });
    Status::from(result)
}

/// Emit: forward the first `output_len` bytes of the loaned buffer as a new input along every
/// outgoing input edge. `output_type` names how to read it; it may be NULL.
///
/// # Safety
/// As for [`gabriel_engine_stop`]; `output_type` must be NUL-terminated or null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_emit(
    engine: *mut Engine,
    output_type: *const c_char,
    output_len: usize,
    result: *const u8,
    result_len: usize,
) -> Status {
    Status::from(unsafe {
        (|| {
            handle_arg(engine)?.emit(
                opt_str_arg(output_type)?.unwrap_or_default(),
                output_len,
                bytes_arg(result, result_len)?,
            )
        })()
    })
}

// --- Subscription ---

/// What happened, as a subscription reports it: the kinds of published events, and `Lost`.
#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventKindView {
    Accepted,
    Sent,
    Result,
    Skipped,
    TimedOut,
    Disconnected,
    Derived,
    /// Events were lost before the next one, because the subscriber fell behind.
    Lost,
}

/// What an engine forwarded, as a subscription reports it.
#[repr(u32)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ForwardView {
    Stop,
    Pass,
    Emit,
}

impl From<Forward> for ForwardView {
    fn from(forward: Forward) -> Self {
        match forward {
            Forward::Stop => Self::Stop,
            Forward::Pass => Self::Pass,
            Forward::Emit => Self::Emit,
        }
    }
}

impl From<EventKind> for EventKindView {
    fn from(kind: EventKind) -> Self {
        match kind {
            EventKind::Accepted => Self::Accepted,
            EventKind::Sent => Self::Sent,
            EventKind::Result => Self::Result,
            EventKind::Skipped => Self::Skipped,
            EventKind::TimedOut => Self::TimedOut,
            EventKind::Disconnected => Self::Disconnected,
            EventKind::Derived => Self::Derived,
        }
    }
}

/// An event read from a subscription. Stays valid until the next poll. For `LOST`, only `lost`
/// is set, and `engine` on a subscription to an engine; for `DISCONNECTED`, only `flow`, and
/// `engine` on a subscription to an engine.
#[repr(C)]
pub struct EventView {
    kind: EventKindView,
    /// For `LOST`: how many events were lost.
    lost: u64,
    id: InputIdView,
    /// The engine the event is about; "" if none.
    engine: *const c_char,
    /// The flow the event belongs to; "" if none.
    flow: *const c_char,
    /// What the engine forwarded, for `RESULT`.
    forward: ForwardView,
    /// How to read `result`; "" if there is no result.
    payload_type: *const c_char,
    result: *const u8,
    result_len: usize,
    /// When the event happened, in nanoseconds on the machine's `CLOCK_MONOTONIC`.
    at_ns: u64,
    /// When the server made the engine's offer, on the same clock; 0 if the client made the
    /// offer, or for `DISCONNECTED`.
    offered_at_ns: u64,
    /// When the client received the event from the remote server, on this machine's
    /// `CLOCK_MONOTONIC`; `at_ns` and `offered_at_ns` are then on the remote server's clock. 0 for
    /// other events.
    received_at_ns: u64,
}

impl EventView {
    fn new(event: &Event<'_>) -> Self {
        match event {
            Event::Lost(lost) => Self {
                kind: EventKindView::Lost,
                lost: lost.count(),
                id: InputIdView {
                    producer: c"".as_ptr(),
                    session: 0,
                    seq: 0,
                },
                engine: lost.engine.map_or(c"".as_ptr(), |engine| engine.as_c_str()),
                flow: c"".as_ptr(),
                forward: ForwardView::Stop,
                payload_type: c"".as_ptr(),
                result: ptr::null(),
                result_len: 0,
                at_ns: 0,
                offered_at_ns: 0,
                received_at_ns: 0,
            },
            Event::Published(published) => {
                let record = published.record;
                Self {
                    kind: record.kind.into(),
                    lost: 0,
                    id: InputIdView {
                        producer: record.id.producer.as_c_str(),
                        session: record.id.session,
                        seq: record.id.seq,
                    },
                    engine: record.engine.as_c_str(),
                    flow: record.flow.as_c_str(),
                    forward: record.forward.into(),
                    payload_type: record.payload_type.as_c_str(),
                    result: published.result().as_ptr(),
                    result_len: published.result().len(),
                    at_ns: published.at().as_nanos(),
                    offered_at_ns: published.offered_at().map_or(0, |at| at.as_nanos()),
                    received_at_ns: published.received_at().map_or(0, |at| at.as_nanos()),
                }
            }
        }
    }
}

/// Subscribes to the events about the inputs of the producer `name`: what happened to each at
/// each engine, and lost connections. Only events published from now on are read. Use and destroy
/// the subscription only on the thread that created it.
///
/// # Safety
/// `name` must be NUL-terminated; `subscription` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_subscribe_producer(
    name: *const c_char,
    subscription: *mut *mut Subscription,
) -> Status {
    unsafe { create_handle(subscription, || Subscription::producer(str_arg(name)?)) }
}

/// Subscribes to the events of the engine `name`: what happened to each input at that engine.
/// Only events published from now on are read. Use and destroy the subscription only on the
/// thread that created it.
///
/// # Safety
/// `name` must be NUL-terminated; `subscription` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_subscribe_engine(
    name: *const c_char,
    subscription: *mut *mut Subscription,
) -> Status {
    unsafe { create_handle(subscription, || Subscription::engine(str_arg(name)?)) }
}

/// # Safety
/// `subscription` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_subscription_destroy(subscription: *mut Subscription) -> Status {
    unsafe { destroy_handle(subscription) }
}

/// Waits until an event is ready, or `timeout_ms` passes, and returns `ERR_AGAIN` then. Fetch the
/// event with `gabriel_subscription_poll`.
///
/// # Safety
/// `subscription` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_subscription_wait(
    subscription: *mut Subscription,
    timeout_ms: u32,
) -> Status {
    let timeout = Duration::from_millis(u64::from(timeout_ms));
    Status::from(unsafe { handle_arg(subscription) }.and_then(|s| s.wait(timeout)))
}

/// Returns `ERR_AGAIN` if no event is available. The event stays valid until the next poll.
///
/// # Safety
/// `subscription` must be a live handle; `event` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_subscription_poll(
    subscription: *mut Subscription,
    event: *mut EventView,
) -> Status {
    if event.is_null() {
        return null_argument("event");
    }
    let result = unsafe { handle_arg(subscription) }
        .and_then(Subscription::poll)
        // SAFETY: `event` is non-null and writable per the caller's contract.
        .map(|polled| unsafe { event.write(EventView::new(&polled)) });
    Status::from(result)
}

#[cfg(test)]
mod tests {
    use std::ffi::CString;

    use super::*;
    use crate::test_util::unique;

    #[test]
    fn destroy_null_is_ok() {
        assert_eq!(
            unsafe { gabriel_producer_destroy(ptr::null_mut()) },
            Status::Ok
        );
    }

    #[test]
    fn create_rejects_null_out_pointer() {
        let status = unsafe { gabriel_producer_create(c"p".as_ptr(), 0, ptr::null_mut()) };
        assert_eq!(status, Status::ErrInvalid);
    }

    #[test]
    fn add_flow_rejects_a_malformed_spec() {
        let name = CString::new(unique("client")).unwrap();
        let server = CString::new(unique("server")).unwrap();
        let mut client = ptr::null_mut();
        let created = unsafe {
            gabriel_client_create(name.as_ptr(), server.as_ptr(), ptr::null(), &mut client)
        };
        assert_eq!(created, Status::Ok);

        let status =
            unsafe { gabriel_client_add_flow(client, c"flow".as_ptr(), c"[[edges]".as_ptr()) };

        assert_eq!(status, Status::ErrInvalid);
        assert_eq!(unsafe { gabriel_client_destroy(client) }, Status::Ok);
    }

    /// Creates a client named after `name` with `options`, and destroys it if that worked.
    fn create_client(options: *const ClientOptions) -> Status {
        let name = CString::new(unique("client")).unwrap();
        let server = CString::new(unique("server")).unwrap();
        let mut client = ptr::null_mut();
        let status =
            unsafe { gabriel_client_create(name.as_ptr(), server.as_ptr(), options, &mut client) };
        assert_eq!(unsafe { gabriel_client_destroy(client) }, Status::Ok);
        status
    }

    fn client_options() -> *mut ClientOptions {
        let mut options = ptr::null_mut();
        assert_eq!(
            unsafe { gabriel_client_options_create(&mut options) },
            Status::Ok
        );
        options
    }

    #[test]
    fn crossing_capacity_needs_a_remote_server() {
        let options = client_options();
        let set = unsafe { gabriel_client_options_set_remote_crossing_capacity(options, 4) };
        assert_eq!(set, Status::Ok);

        assert_eq!(create_client(options), Status::ErrInvalid);
        assert_eq!(
            unsafe { gabriel_client_options_destroy(options) },
            Status::Ok
        );
    }

    #[test]
    fn zero_crossing_capacity_is_rejected() {
        let options = client_options();
        let set = unsafe {
            gabriel_client_options_set_remote_server(
                options,
                c"127.0.0.1:4433".as_ptr(),
                ptr::null(),
                0,
            )
        };
        assert_eq!(set, Status::Ok);
        unsafe { gabriel_client_options_set_remote_crossing_capacity(options, 0) };

        assert_eq!(create_client(options), Status::ErrInvalid);
        unsafe { gabriel_client_options_destroy(options) };
    }

    #[test]
    fn remote_server_address_is_checked_when_set() {
        let options = client_options();

        let set = unsafe {
            gabriel_client_options_set_remote_server(
                options,
                c"not an address".as_ptr(),
                ptr::null(),
                0,
            )
        };

        assert_eq!(set, Status::ErrInvalid);
        unsafe { gabriel_client_options_destroy(options) };
    }

    #[test]
    fn identity_dir_needs_a_bind_address() {
        let mut options = ptr::null_mut();
        unsafe { gabriel_server_options_create(&mut options) };
        unsafe { gabriel_server_options_set_identity_dir(options, c"/tmp".as_ptr()) };
        let name = CString::new(unique("server")).unwrap();
        let mut server = ptr::null_mut();

        let status = unsafe { gabriel_server_create(name.as_ptr(), options, &mut server) };

        assert_eq!(status, Status::ErrInvalid);
        assert!(server.is_null());
        unsafe { gabriel_server_options_destroy(options) };
    }

    #[test]
    fn abi_version_matches_the_header() {
        assert_eq!(gabriel_abi_version(), ABI_VERSION);
    }

    #[test]
    fn subscription_without_events_is_again() {
        let name = CString::new(unique("camera")).unwrap();
        let mut subscription = ptr::null_mut();
        let created = unsafe { gabriel_subscribe_producer(name.as_ptr(), &mut subscription) };
        assert_eq!(created, Status::Ok);
        let mut event = std::mem::MaybeUninit::<EventView>::uninit();

        let polled = unsafe { gabriel_subscription_poll(subscription, event.as_mut_ptr()) };

        assert_eq!(polled, Status::ErrAgain);
        assert_eq!(
            unsafe { gabriel_subscription_destroy(subscription) },
            Status::Ok
        );
    }

    #[test]
    fn last_error_says_what_failed() {
        let mut producer = ptr::null_mut();
        unsafe { gabriel_producer_create(ptr::null(), 0, &mut producer) };

        let message = unsafe { CStr::from_ptr(gabriel_last_error()) };

        assert_eq!(message, c"invalid argument: null string");
    }

    #[test]
    fn create_rejects_null_name() {
        let mut producer = ptr::null_mut();
        let status = unsafe { gabriel_producer_create(ptr::null(), 0, &mut producer) };
        assert_eq!(status, Status::ErrInvalid);
        assert!(producer.is_null());
    }
}
