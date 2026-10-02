//! C ABI. Every function returns a [`Status`]; handles are opaque pointers
//! created by `*_create` and freed by `*_destroy`.

use std::ffi::{CStr, c_char};

use crate::{Client, Engine, Error, Producer, Result, Server};

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

/// # Safety
/// `p` must be a live handle; `flow` must be NUL-terminated.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_producer_publish_input(
    p: *mut Producer,
    len: usize,
    flow: *const c_char,
) -> Status {
    Status::from(unsafe { (|| handle_arg(p)?.publish_input(len, str_arg(flow)?))() })
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

/// # Safety
/// String arguments must be NUL-terminated; `client` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_client_add_flow(
    name: *const c_char,
    spec: *const c_char,
    client: *mut Client,
) -> Status {
    Status::from(unsafe { (|| handle_arg(client)?.add_flow(str_arg(name)?, str_arg(spec)?))() })
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

/// # Safety
/// String arguments must be NUL-terminated; `server` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_server_create(
    name: *const c_char,
    bind_addr: *const c_char,
    server: *mut *mut Server,
) -> Status {
    unsafe { create_handle(server, || Server::new(str_arg(name)?, str_arg(bind_addr)?)) }
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

/// # Safety
/// String arguments must be NUL-terminated; `engine` must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_create(
    name: *const c_char,
    max_result_size: usize,
    engine: *mut *mut Engine,
) -> Status {
    unsafe { create_handle(engine, || Engine::new(str_arg(name)?, max_result_size)) }
}

/// # Safety
/// `engine` must be null or a live handle that is not used afterwards.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_engine_destroy(engine: *mut Engine) -> Status {
    unsafe { destroy_handle(engine) }
}

/// Polls for the next input. `*buf`, `*len` and `*producer` stay valid until
/// `gabriel_input_release`. Returns `ERR_AGAIN` if nothing is available.
///
/// # Safety
/// `engine` must be a live handle; the out-pointers must be valid for writes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_input_poll(
    engine: *mut Engine,
    buf: *mut *const u8,
    len: *mut usize,
    producer: *mut *const c_char,
) -> Status {
    if buf.is_null() || len.is_null() || producer.is_null() {
        return Status::ErrInvalid;
    }
    let result = unsafe { handle_arg(engine) }
        .and_then(Engine::poll_input)
        .map(|input| unsafe {
            *buf = input.data.as_ptr();
            *len = input.data.len();
            *producer = input.producer.as_ptr();
        });
    Status::from(result)
}

/// # Safety
/// `engine` must be a live handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_input_release(engine: *mut Engine) -> Status {
    Status::from(unsafe { handle_arg(engine) }.and_then(Engine::release_input))
}

/// # Safety
/// `engine` must be a live handle; `producer` must be NUL-terminated; `buf`
/// must point to `len` readable bytes (or be null when `len` is 0).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn gabriel_publish_result(
    engine: *mut Engine,
    producer: *const c_char,
    buf: *mut u8,
    len: usize,
) -> Status {
    let result = if buf.is_null() && len > 0 {
        Err(Error::InvalidArgument("null buffer".into()))
    } else {
        let data: &[u8] = if len == 0 {
            &[]
        } else {
            // SAFETY: `buf` is non-null and points to `len` bytes per the contract.
            unsafe { std::slice::from_raw_parts(buf, len) }
        };
        unsafe { (|| handle_arg(engine)?.publish_result(str_arg(producer)?, data))() }
    };
    Status::from(result)
}

#[cfg(test)]
mod tests {
    use std::ptr;

    use super::*;

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
    fn create_rejects_null_name() {
        let mut producer = ptr::null_mut();
        let status = unsafe { gabriel_producer_create(ptr::null(), 0, &mut producer) };
        assert_eq!(status, Status::ErrInvalid);
        assert!(producer.is_null());
    }
}
