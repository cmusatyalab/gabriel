pub(crate) mod types;

use std::mem::MaybeUninit;

/// Views initialized bytes as possibly uninitialized ones, for copying into a loaned buffer.
pub(crate) fn as_uninit(bytes: &[u8]) -> &[MaybeUninit<u8>] {
    // SAFETY: `MaybeUninit<u8>` has the same layout as `u8`, and every `u8` is a valid one.
    unsafe { std::slice::from_raw_parts(bytes.as_ptr().cast(), bytes.len()) }
}
