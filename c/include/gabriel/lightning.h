#ifndef GABRIEL_LIGHTNING_H
#define GABRIEL_LIGHTNING_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#include "gabriel/errors.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Lightning is the low-level transport: connect/bind/send/recv plus
 * per-connection token-based flow control. The gabriel_* API (see
 * gabriel.h) is the protocol layer built on top of it. */

typedef struct lightning_connection_t lightning_connection_t;

/* A listener created by lightning_bind(), distinct from a connection:
 * it accepts new connections via lightning_accept() but is never
 * itself something you send/recv on. */
typedef struct lightning_listener_t lightning_listener_t;

/* A single message on the wire. `source_name` disambiguates which
 * producer a message is from/for on a connection that fans in/out
 * several of them. `token` marks the message as also carrying a
 * token grant. `meta`/`meta_size` and `data`/`data_size` are separate
 * buffers so protocol metadata doesn't have to share an allocation
 * with the payload. */
typedef struct lightning_message_t {
  const char *source_name;
  bool token;
  uint64_t meta_size;
  int8_t *meta;
  uint64_t data_size;
  int8_t *data;
} lightning_message_t;

/* Severity passed to a callback registered with
 * lightning_set_log_callback(). */
typedef enum {
  LIGHTNING_LOG_DEBUG,
  LIGHTNING_LOG_INFO,
  LIGHTNING_LOG_WARN,
  LIGHTNING_LOG_ERROR,
} lightning_log_level_t;

typedef void (*lightning_log_fn)(lightning_log_level_t level,
                                  const char *message, void *user_data);

/* Registers `fn` to receive Lightning's internal diagnostic messages
 * (handshake failures, dropped connections, etc.), with `user_data`
 * passed through unchanged on every call. Lightning is silent by
 * default - nothing is logged until a callback is registered. Pass
 * NULL to stop logging.
 *
 * Not safe to call concurrently with Lightning I/O on another thread;
 * intended to be set once during startup before connections are
 * made. */
void lightning_set_log_callback(lightning_log_fn fn, void *user_data);

/* Connects to a Lightning listener at `address` ("tcp://host:port" or
 * "unix://path"). For "tcp://", `max_tokens` is this connection's send
 * budget - send() drops a message if none are available. For
 * "unix://", flow control instead works by passing a single-slot
 * write-lock back and forth per direction (see lightning_send()); a
 * dedicated arena is created for this side and exchanged with the
 * peer, and `shm_size` requests its size (ignored for "tcp://"; pass 0
 * for a default).
 *
 * Blocks while it hands `max_tokens` (and, for "unix://", `shm_size`
 * and its own shared memory arena) to the server as part of the
 * connection handshake and waits for the server to reciprocate; fails
 * (returning NULL) if the peer never does. For "unix://", the arena
 * this side reads from is available via lightning_shared_memory().
 *
 * `error`, if non-NULL, is set to the specific reason on failure (and
 * to LIGHTNING_OK on success). Pass NULL if you only care whether it
 * succeeded. */
lightning_connection_t *lightning_connect(const char *address, int max_tokens,
                                           size_t shm_size,
                                           lightning_error_t *error);

/* Starts listening at `address` for incoming connections. `error`
 * follows the same convention as lightning_connect()'s. */
lightning_listener_t *lightning_bind(const char *address,
                                      lightning_error_t *error);

/* Blocks until a new connection arrives on `listener`, returning it.
 * Each accepted connection is independent - callers accept in a loop
 * to serve more than one at a time. An accepted connection is never
 * send-gated the way the lightning_connect() side is: it can call
 * lightning_send() whenever it wants (still subject to the "unix://"
 * single-slot lock, same as any other connection).
 *
 * As part of accepting, blocks while it receives the client's
 * declared `max_tokens` and (for "unix://") requested shared memory
 * size and its arena (see lightning_connect()), and reciprocates.
 * Fails (returning NULL) if the handshake doesn't complete.
 *
 * `error` follows the same convention as lightning_connect()'s. */
lightning_connection_t *lightning_accept(lightning_listener_t *listener,
                                          lightning_error_t *error);

/* For a connection accepted or made over a "unix://" address, fills
 * `fd` and `size` with the arena this side reads from (the one the
 * peer created and shared) and returns 0. Returns -1 (leaving
 * `fd`/`size` untouched) for a "tcp://" connection, which has no
 * shared memory. lightning_send()/lightning_recv() already use this
 * arena internally - direct access is only useful for diagnostics. */
int lightning_shared_memory(const lightning_connection_t *conn, int *fd,
                             size_t *size);

/* Sends `msg`. On "tcp://", returns LIGHTNING_OK if sent, or
 * LIGHTNING_ERR_NO_TOKEN if dropped because no token was available.
 * On "unix://", returns LIGHTNING_OK if sent, or
 * LIGHTNING_ERR_NO_TOKEN if this side doesn't currently hold its
 * arena's write-lock (the peer hasn't finished reading the previous
 * message yet) - never blocks waiting for it. Either way, may return
 * another lightning_error_t on I/O failure or an oversized message. */
lightning_error_t lightning_send(lightning_connection_t *conn,
                                  const lightning_message_t *msg);

/* Fills in `msg` with the next message. On "tcp://", blocks until one
 * arrives; a message with `token` set replenishes the connection's
 * send budget by one. On "unix://", never blocks - returns
 * LIGHTNING_ERR_WOULD_BLOCK immediately if nothing is waiting yet
 * (`token` is managed internally on this transport, not
 * caller-facing). Returns LIGHTNING_OK on success, or another
 * lightning_error_t on failure/disconnection. */
lightning_error_t lightning_recv(lightning_connection_t *conn,
                                  lightning_message_t *msg);

/* Frees the buffers a successful lightning_recv() allocated into
 * `msg` (`source_name`, `meta`, `data`). Safe to call on a message
 * lightning_recv() never filled in. */
void lightning_message_free(lightning_message_t *msg);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GABRIEL_LIGHTNING_H */
