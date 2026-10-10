//! C ABI. Every function returns a [`Status`]; handles are opaque pointers
//! created by `*_create` and freed by `*_destroy`.

use std::{
    ffi::{CStr, c_char},
    ptr,
    time::Duration,
};

use iceoryx2_bb_container::string::String as _;

use crate::{
    Client, Engine, Error, EventKind, FlowSpec, Forward, Producer, Result, Server, Subscription,
    logging, subscription::Event,
};

/// Status code returned by every C ABI function.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ok = 0,
    ErrInvalid,
    ErrSizeExceeded,
    ErrAgain,
    ErrInternal,
}

impl From<&Error> for Status {
    fn from(err: &Error) -> Self {
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
        return Status::ErrInvalid;
    }
    let result = create().map(|value| {
        // SAFETY: `out` is non-null and writable per the caller's contract.
        unsafe { *out = Box::into_raw(Box::new(value)) };
    });
    Status::from(result)
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
/// `p` must be a live handle; `buf` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_producer_new_input(p: *mut Producer, buf: *mut *mut u8) -> Status {
    if buf.is_null() {
        return Status::ErrInvalid;
    }
    let result = unsafe { handle_arg(p) }
        .and_then(Producer::new_input)
        .map(|input| unsafe { *buf = input.as_mut_ptr().cast() });
    Status::from(result)
}

/// Publishes the first `len` bytes of the input on `flow`. If `seq` is not null, it gets the
/// input's sequence number, which the events about the input carry.
///
/// # Safety
/// `p` must be a live handle; `flow` must be NUL-terminated; `seq` must be null or valid for
/// writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_producer_publish_input(
    p: *mut Producer,
    len: usize,
    flow: *const c_char,
    seq: *mut u64,
) -> Status {
    let result = unsafe { (|| handle_arg(p)?.publish_input(len, str_arg(flow)?))() };
    Status::from(result.map(|id| {
        if !seq.is_null() {
            unsafe { seq.write(id.seq()) };
        }
    }))
}

// --- Client ---

/// # Safety
/// String arguments must be NUL-terminated; `client` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_create(
    name: *const c_char,
    local_server: *const c_char,
    remote_server_addr: *const c_char,
    client: *mut *mut Client,
) -> Status {
    unsafe {
        create_handle(client, || {
            Client::new(
                str_arg(name)?,
                str_arg(local_server)?,
                str_arg(remote_server_addr)?,
            )
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
/// `name` must be NUL-terminated; `client` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_add_producer(
    name: *const c_char,
    client: *mut Client,
) -> Status {
    Status::from(unsafe { (|| handle_arg(client)?.add_producer(str_arg(name)?))() })
}

/// # Safety
/// `name` must be NUL-terminated; `client` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_remove_producer(
    name: *const c_char,
    client: *mut Client,
) -> Status {
    Status::from(unsafe { (|| handle_arg(client)?.remove_producer(str_arg(name)?))() })
}

/// Adds the flow `name`. `spec` describes it in TOML, as a list of edges; see the `spec` module
/// of the Rust API for an example.
///
/// # Safety
/// String arguments must be NUL-terminated; `client` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_add_flow(
    name: *const c_char,
    spec: *const c_char,
    client: *mut Client,
) -> Status {
    Status::from(unsafe {
        (|| {
            let spec: FlowSpec = str_arg(spec)?.parse()?;
            handle_arg(client)?.add_flow(str_arg(name)?, &spec)
        })()
    })
}

/// # Safety
/// `name` must be NUL-terminated; `client` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_remove_flow(
    name: *const c_char,
    client: *mut Client,
) -> Status {
    Status::from(unsafe { (|| handle_arg(client)?.remove_flow(str_arg(name)?))() })
}

// --- Server ---

/// `bind_addr` is the network address to accept remote clients on, or NULL for a local server.
///
/// # Safety
/// String arguments must be NUL-terminated (`bind_addr` may be null); `server` must be valid for
/// writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_create(
    name: *const c_char,
    bind_addr: *const c_char,
    server: *mut *mut Server,
) -> Status {
    unsafe {
        create_handle(server, || {
            Server::new(str_arg(name)?, opt_str_arg(bind_addr)?)
        })
    }
}

/// # Safety
/// `server` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_destroy(server: *mut Server) -> Status {
    unsafe { destroy_handle(server) }
}

/// # Safety
/// `name` must be NUL-terminated; `server` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_add_engine(
    name: *const c_char,
    server: *mut Server,
) -> Status {
    Status::from(unsafe { (|| handle_arg(server)?.add_engine(str_arg(name)?))() })
}

/// # Safety
/// `name` must be NUL-terminated; `server` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_remove_engine(
    name: *const c_char,
    server: *mut Server,
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

/// `result_type` names how to read this engine's results, e.g. a protobuf message name. It may be
/// NULL.
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

/// Waits until an input is ready, or `timeout_ms` passes, and returns `ERR_AGAIN` then. Fetch
/// the input with `gabriel_engine_poll_input`, which may still return `ERR_AGAIN`.
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
        return Status::ErrInvalid;
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
        return Status::ErrInvalid;
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
#[repr(C)]
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
/// is set; for `DISCONNECTED`, only `flow`.
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
    forward: Forward,
    /// How to read `result`; "" if there is no result.
    payload_type: *const c_char,
    result: *const u8,
    result_len: usize,
    /// When the event happened, in nanoseconds on the machine's `CLOCK_MONOTONIC`.
    at_ns: u64,
    /// When the server made the engine's offer, on the same clock; 0 if the client made the
    /// offer, or for `DISCONNECTED`.
    offered_at_ns: u64,
}

impl EventView {
    fn new(event: &Event<'_>) -> Self {
        match event {
            Event::Lost { count } => Self {
                kind: EventKindView::Lost,
                lost: *count,
                id: InputIdView {
                    producer: c"".as_ptr(),
                    session: 0,
                    seq: 0,
                },
                engine: c"".as_ptr(),
                flow: c"".as_ptr(),
                forward: Forward::default(),
                payload_type: c"".as_ptr(),
                result: ptr::null(),
                result_len: 0,
                at_ns: 0,
                offered_at_ns: 0,
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
                    forward: record.forward,
                    payload_type: record.payload_type.as_c_str(),
                    result: published.result().as_ptr(),
                    result_len: published.result().len(),
                    at_ns: published.at().as_nanos(),
                    offered_at_ns: published.offered_at().map_or(0, |at| at.as_nanos()),
                }
            }
        }
    }
}

/// Subscribes to the events about the inputs of the producer `name`: what happened to each at
/// each engine, and lost connections. Only events published from now on are read.
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
        return Status::ErrInvalid;
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
            gabriel_client_create(name.as_ptr(), server.as_ptr(), c"".as_ptr(), &mut client)
        };
        assert_eq!(created, Status::Ok);

        let status =
            unsafe { gabriel_client_add_flow(c"flow".as_ptr(), c"[[edges]".as_ptr(), client) };

        assert_eq!(status, Status::ErrInvalid);
        assert_eq!(unsafe { gabriel_client_destroy(client) }, Status::Ok);
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
    fn create_rejects_null_name() {
        let mut producer = ptr::null_mut();
        let status = unsafe { gabriel_producer_create(ptr::null(), 0, &mut producer) };
        assert_eq!(status, Status::ErrInvalid);
        assert!(producer.is_null());
    }
}
