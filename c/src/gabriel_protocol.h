#ifndef GABRIEL_PROTOCOL_H
#define GABRIEL_PROTOCOL_H

#include <stdbool.h>
#include <stdint.h>

#include "gabriel/gabriel.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Internal wire protocol for Gabriel, a token-based flow control peer to
 * peer producer/consumer protocol. Not part of the public API - see
 * gabriel.h for that. Tokens are used to pace out data so that the
 * producer doesn't transmit frames that won't be consumed. To this end,
 * producers used AIMD to dynamically adjust token counts to drop
 * signals. Producers can also be configured to not use tokens by
 * setting `max_tokens` to 0 on creation.
 *
 * This protocol supports two different types of connections:
 *
 * 1. buffered [address prefixed by tcp:/unix:] sends a frame to all consumers
 * by queueing it into the socket, so long as a token is available. Consumers
 * receive every frame that is sent as long as the connection is alive.
 *
 * 2. unbuffered [address prefixed by tcp:/unix:/shm:] sends a frame to all consumers
 * through a socket or shared memory exchange, so long as a token is available.
 * Consumers only receive the newest frame available, older frames are dropped.
 *
 * Consumers are expected to reply() to every recv() call with a token. */

/* Forward declarations of producer and consumer types. */
typedef struct gabriel_producer_t gabriel_producer_t;
typedef struct gabriel_consumer_t gabriel_consumer_t;

/* Creates a producer. `name` is the name used to populate the `source_name` field for this
 * producer ("" if NULL). Must be less or equal to than GABRIEL_MAX_NAME characters long.
 * `max_tokens` is the size of the producer's token bucket, capped at GABRIEL_MAX_TOKENS.
 * A `max_tokens` of 0 ignores token gating. `max_send_size` is the maximum size of a send
 * payload. This defaults to GABRIEL_DEFAULT_MAX_MSG_SIZE if 0. `mode` controls whether
 * the producer fans out to all consumers on a send, or just sends to the next ready consumer
 * round-robin. This defaults to GABRIEL_PRODUCER_MODE_FANOUT. Returns NULL and sets `error`
 * on failure. */
gabriel_producer_t *gabriel_new_producer(const char *name, uint32_t max_tokens,
        uint64_t max_send_size, gabriel_producer_mode_t mode, gabriel_error_t *error);

/* Adds a consumer to a producer's consumer list. Returns GABRIEL_ERR_FULL if
 * there are GABRIEL_MAX_CONSUMERS consumers already. */
gabriel_error_t gabriel_add_consumer_to_producer(gabriel_producer_t *producer,
        const char *consumer_address);

/* Removes a consumer to a producer's consumer list. Returns GABRIEL_ERR_INVALID if
 * the consumer does not exist. */
gabriel_error_t gabriel_remove_consumer_from_producer(gabriel_producer_t *producer,
        const char *consumer_address);

/* Produces a frame and and sends it to all available consumers. If `token_gated`, will
 * drop frames if no token is available. `seq_num` should strictly increase for each call.
 * `metadata`/`metadata_size` is optional out-of-band data (e.g. a result) carried
 * alongside `data`/`data_size`. */
gabriel_error_t gabriel_produce(gabriel_producer_t *producer, const uint8_t *metadata,
        uint64_t metadata_size, const uint8_t *data, uint64_t data_size, uint64_t seq_num);

/* Checks for one reply and returns it if one is ready. This is a
 * non-blocking, poll-style call - it checks, doesn't wait, and returns
 * immediately either way, so it is meant to be called again by the
 * caller (immediately, or after a short sleep) rather than blocked on.
 *
 * How it picks which consumer to check, and what happens if that
 * consumer's connection turns out to be broken, differs by the
 * producer's mode:
 *
 * - FANOUT: searches forward from wherever the last call left off for
 *   the next consumer that is connected. If that consumer's connection
 *   is broken, a reconnect is started for it in the background and
 *   GABRIEL_ERR_AGAIN is returned (as if it simply had nothing ready) -
 *   the search just moves past it on the next call, the same as any
 *   other consumer with nothing pending.
 * - SEQUENTIAL: only ever checks the one consumer at the current
 *   position (no searching). If that consumer's connection is broken,
 *   a reconnect is started for it in the background, the position is
 *   reset to the beginning, and GABRIEL_ERR_INTERNAL is returned rather
 *   than GABRIEL_ERR_AGAIN, since - unlike FANOUT - there is no other
 *   consumer to fall back to this call.
 *
 * Returns NULL and sets `error` to:
 *   GABRIEL_ERR_INVALID     no consumers are attached to this producer.
 *   GABRIEL_ERR_CONNECTING  the consumer to check isn't connected yet
 *                           (FANOUT: none currently are).
 *   GABRIEL_ERR_AGAIN       nothing is ready right now; try again.
 *   GABRIEL_ERR_INTERNAL    (SEQUENTIAL only) the connection broke.
 * On success, returns the reply and leaves `error` set to GABRIEL_OK. */
gabriel_message_t *gabriel_read_reply(gabriel_producer_t *producer, gabriel_error_t *error);

/* Creates a consumer. `name` is the name used to populate the `source_name` field for this
 * consumer. Must be less than or equal to GABRIEL_MAX_NAME characters long. `address` is
 * the bind address. If tcp:, only uses the port. If unix: or shm:, creates a Unix Domain
 * Socket at that path. `max_send_size` is the maximum size of the send payload (defaults to
 * GABRIEL_DEFAULT_MSG_SIZE if set to 0). `buffered` sets the consumer to queue frames rather
 * than drop until the most recent frame. Returns NULL and sets `error` on failure. Note: shm:
 * addresses are incompatible with `buffered` connections and will cause GABRIEL_ERR_INVALID. */
gabriel_consumer_t *gabriel_new_consumer(const char *name, const char *address,
        uint64_t max_send_size, bool buffered, gabriel_error_t *error);

/* Consumes a message from the next available producer round-robin. This is guaranteed to
 * be the latest frame received by that producer if the producer has set `buffered` to `false`.
 * Returns NULL and sets `error` on failure. */
gabriel_message_t *gabriel_consume(gabriel_consumer_t *consumer, gabriel_error_t *error);

/* Replies to the frame most recently returned by gabriel_consume(), which returns
 * its token to the producer. Each frame should generate exactly one reply. Multiple calls
 * for a single gabriel_consume() or a call without first calling consume will return
 * GABRIEL_ERR_INVALID. */
gabriel_error_t gabriel_send_reply(gabriel_consumer_t *consumer, const uint8_t *data,
        uint64_t data_size);

/* Cleanup methods. */
void gabriel_free_message(gabriel_message_t *message);
void gabriel_free_producer(gabriel_producer_t *producer);
void gabriel_free_consumer(gabriel_consumer_t *consumer);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GABRIEL_PROTOCOL_H */
