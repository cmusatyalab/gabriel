#ifndef LIGHTNING_H
#define LIGHTNING_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Lightning is a token-based flow control producer-consumer transport.
 * A producer fans frames out to any number of consumers (targets), and
 * a consumer fans frames in from any number of producers and replies
 * to them. Each connection is buffered ("tcp://host:port" or
 * "unix://path": data over the socket, pushed while a token is
 * available) or unbuffered ("shm://path": data in shared memory, the
 * newest frame pulled whenever the consumer returns a token). See
 * README.md for the full semantics. */

typedef enum {
  LIGHTNING_OK = 0,
  LIGHTNING_ERR_DROPPED,     /* send: no consumer could take the frame */
  LIGHTNING_ERR_TOO_LARGE,   /* data_size > max_send_size */
  LIGHTNING_ERR_BROKEN_PIPE, /* the peer disconnected */
  LIGHTNING_ERR_INVALID,     /* bad address/argument, or reply before recv */
  LIGHTNING_ERR_FULL,        /* add_target: already at 31 targets */
  LIGHTNING_ERR_CLOSED,      /* the handle was destroyed */
  LIGHTNING_ERR_INTERNAL,    /* internal failure catch-all */
} lightning_error_t;

typedef enum {
  LIGHTNING_TOKEN_NONE = 0,
  LIGHTNING_TOKEN_ACCEPT = 1,
  LIGHTNING_TOKEN_DROP = 2,
} lightning_token_t;

/* A received frame (from lightning_recv()) or reply (from
 * lightning_recv_reply()). The source_* fields describe the sender.
 * Everything is owned by the message; free it with
 * lightning_message_free(). */
typedef struct lightning_message_t {
  const char *source_name; /* sender's source_name */
  uint64_t source_id;      /* sender's random unique ID */
  const char *source_ip;   /* sender's IP, "" if unavailable (Unix socket) */
  const char *source_host; /* sender's host_name, "" if not set */
  uint32_t seq_num;        /* frame seq_num (or the frame a reply answers) */
  lightning_token_t token; /* ACCEPT on replies, NONE on frames */
  uint64_t data_size;
  uint8_t *data;
} lightning_message_t;

typedef struct lightning_producer_t lightning_producer_t;
typedef struct lightning_consumer_t lightning_consumer_t;

/* Maximum number of targets a producer can have at once. */
#define LIGHTNING_MAX_TARGETS 31

/* ---- General ---- */

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
 * default. Pass NULL to stop logging. `fn` may be called from
 * Lightning's background threads.
 *
 * Not safe to call concurrently with other Lightning calls; intended
 * to be set once during startup. */
void lightning_set_log_callback(lightning_log_fn fn, void *user_data);

/* Returns the library version string, e.g. "0.1.0". */
const char *lightning_version(void);

/* Frees a message returned by lightning_recv() or
 * lightning_recv_reply(). Safe to call with NULL. */
void lightning_message_free(lightning_message_t *msg);

/* ---- Producer ---- */

/* Creates a producer. `targets` is an optional NULL-terminated list of
 * initial targets, each added as if by lightning_add_target(); pass
 * NULL to start with none. `max_tokens` is the size of each consumer's
 * token bucket, `max_send_size` the largest frame this producer sends,
 * and `stale_seqs` how many seq_nums behind the newest a frame must be
 * to count as stale (0 disables). `host_name` may be NULL. Returns NULL
 * and sets `error` (if non-NULL) on failure. */
lightning_producer_t *lightning_create_producer(
    uint32_t max_tokens, uint64_t max_send_size, uint32_t stale_seqs,
    const char *source_name, const char *host_name, const char **targets,
    lightning_error_t *error);

/* Stops the background threads, closes all connections, and frees the
 * producer. Calls blocked on it on other threads return
 * LIGHTNING_ERR_CLOSED. */
void lightning_destroy_producer(lightning_producer_t *producer);

/* Adds a target. Tries to connect once synchronously; if the consumer
 * isn't reachable, the target is still added and retried in the
 * background. Returns LIGHTNING_ERR_INVALID for a malformed or
 * duplicate address, LIGHTNING_ERR_FULL at LIGHTNING_MAX_TARGETS. */
lightning_error_t lightning_add_target(lightning_producer_t *producer,
                                       const char *address);

/* Removes a target added with the same `address` string. Returns
 * LIGHTNING_ERR_INVALID if it isn't a target. */
lightning_error_t lightning_remove_target(lightning_producer_t *producer,
                                          const char *address);

/* Sends a frame to every buffered consumer with a token, and publishes
 * it for unbuffered consumers. `seq_num` must increase with each send.
 * Returns LIGHTNING_ERR_DROPPED if nobody could take it,
 * LIGHTNING_ERR_TOO_LARGE if data_size > max_send_size. */
lightning_error_t lightning_send(lightning_producer_t *producer,
                                 const uint8_t *data, uint64_t data_size,
                                 uint32_t seq_num);

/* Blocks until a reply from any consumer is available and returns it.
 * Returns NULL and sets `error` on failure. */
lightning_message_t *lightning_recv_reply(lightning_producer_t *producer,
                                          lightning_error_t *error);

/* ---- Consumer ---- */

/* Creates a consumer bound at `address` ("tcp://host:port",
 * "unix://path" or "shm://path"; the last two are equivalent and
 * accept both buffered and unbuffered producers). `max_send_size` is
 * the largest reply this consumer sends. `reply_chunk_count` is the
 * number of shared memory reply chunks (0 for a default; ignored for
 * tcp://). `host_name` may be NULL. Returns NULL and sets `error` (if
 * non-NULL) on failure. */
lightning_consumer_t *lightning_create_consumer(const char *source_name,
                                                const char *host_name,
                                                uint64_t max_send_size,
                                                const char *address,
                                                uint32_t reply_chunk_count,
                                                lightning_error_t *error);

/* Stops the accept thread, closes all connections, removes the socket
 * file for a Unix socket address, and frees the consumer. Calls
 * blocked on it on other threads return LIGHTNING_ERR_CLOSED. */
void lightning_destroy_consumer(lightning_consumer_t *consumer);

/* Blocks until a frame from any producer is available (round-robin
 * across producers, stale frames dropped) and returns it. Returns NULL
 * and sets `error` on failure. Must be called from the same thread as
 * lightning_reply(). */
lightning_message_t *lightning_recv(lightning_consumer_t *consumer,
                                    lightning_error_t *error);

/* Replies to the producer of the frame most recently returned by
 * lightning_recv(), returning its token. `seq_num` should be that
 * frame's seq_num. */
lightning_error_t lightning_reply(lightning_consumer_t *consumer,
                                  const uint8_t *data, uint64_t data_size,
                                  uint32_t seq_num);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* LIGHTNING_H */
