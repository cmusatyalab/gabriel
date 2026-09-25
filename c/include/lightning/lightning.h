#ifndef LIGHTNING_H
#define LIGHTNING_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Lightning is a token-based flow control peer to peer producer consumer
 * library. Tokens are used to pace out data so that the producer doesn't
 * transmit frames that won't be consumed. To this end, producers used AIMD
 * to dynamically adjust token counts to drop signals. Producers can also
 * be configured to not use tokens by setting `token_gated` to false on
 * creation.
 *
 * Lightning supports two different types of connections:
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

/* Maximum consumers for a single producer, which sizes the producer's shared
 * memory pool for unbuffered connections. */
#define LIGHTNING_MAX_CONSUMERS 32

/* Default message size for a lightning_message_t data array. */
#define LIGHTNING_DEFAULT_MSG_SIZE 4000u /* 4 kilobytes */

/* --- Declarations --- */

/* Log levels for log callback. */
typedef enum {
    LIGHTNING_LOG_DEBUG,
    LIGHTNING_LOG_INFO,
    LIGHTNING_LOG_WARN,
    LIGHTNING_LOG_ERROR,
} lightning_log_level_t;
 
/* Log callback function declaration. */
typedef void (*lightning_log_fn)(lightning_log_level_t level,
        const char *message, void *user_data);

/* Error messages for lightning functions. */
typedef enum {
    LIGHTNING_OK = 0,            /* no error */
    LIGHTNING_ERR_DROPPED,       /* not enough tokens to send, backpressure signal */
    LIGHTNING_ERR_SIZE_EXCEEDED, /* data_size is too large for producer max_send_size */
    LIGHTNING_ERR_BROKEN_PIPE,   /* peer is disconnected */
    LIGHTNING_ERR_INVALID,       /* bad argument */
    LIGHTNING_ERR_FULL,          /* at LIGHTNING_MAX_CONSUMERS consumers */
    LIGHTNING_ERR_INTERNAL,      /* internal failure */
} lightning_error_t;

/* Basic message exchange type. */
typedef struct lightning_message_t {
    const char *source_name; /* source name of the sender */
    const char *source_ip;   /* source IP address of the sender */
    const char *host_name;   /* source host name (machine name) of the sender */
    uint64_t source_id;      /* lightning generated UUID for the source */
    uint32_t seq_num;        /* frame sequence number, strictly increasing */
    uint64_t data_size;      /* size of data array */
    uint8_t *data;           /* data array pointer */
} lightning_message_t;

/* Forward declarations of producer and consumer types. */
typedef struct lightning_producer_t lightning_producer_t;
typedef struct lightning_consumer_t lightning_consumer_t;

/* --- API --- */

/* Returns the library version string. */
const char *lightning_version(void);

/* Sets the log callback for all lightning functions. This is not threadsafe
 * with other lightning calls, and should be set *before* calling any other
 * lightning functions. */
void lightning_set_log_callback(lightning_log_fn fn, void *user_cb);

/* Creates a producer. token_gated` controls whether the producer uses the
 * token mechanism to send (or ignore tokens). max_tokens` is the size of the 
 * producer's token bucket. `max_send_size` is the maximum size of a send payload. 
 * This defaults to LIGHTNING_DEFAULT_MSG_SIZE if 0. Returns NULL and sets `error`
 * on failure. */
lightning_producer_t *lightning_create_producer(bool token_gated, uint32_t max_tokens,
        uint64_t max_send_size, lightning_error_t *error);

/* Adds a consumer to a producer's consumer list. Returns LIGHTNING_ERR_FULL if
 * there are LIGHTNING_MAX_CONSUMERS consumers already. */
lightning_error_t lightning_add_consumer_to_producer(lightning_producer_t *producer,
        const char *consumer_address);

/* Removes a consumer to a producer's consumer list. Returns LIGHTNING_ERR_INVALID if
 * the consumer does not exist. */
lightning_error_t lightning_remove_consumer_from_producer(lightning_producer_t *producer,
        const char *consumer_address);

/* Produces a frame and and sends it to all available consumers. If `token_gated`, will
 * drop frames if no token is available. `seq_num` should strictly increase for each call. */
lightning_error_t lightning_produce(lightning_producer_t *producer, const uint8_t *data,
        uint64_t data_size, uint32_t seq_num);

/* Creates a consumer bound at `address`. If tcp:, only uses the port. If unix: or shm:, 
 * creates a Unix Domain Socket at that path. `max_send_size` is the maximum size of the
 * send payload (defaults to LIGHTNING_DEFAULT_MSG_SIZE if set to 0). `buffered` sets the
 * consumer to queue frames rather than drop until the most recent frame. Returns NULL and 
 * sets `error` on failure. Note: shm: addresses are incompatible with `buffered` connections
 * and will cause LIGHTNING_ERR_INVALID. */
lightning_consumer_t *lightning_create_consumer(const char *address, uint64_t max_send_size,
        bool buffered, lightning_error_t *error);

/* Consumes a message from the next available producer round-robin. This is guaranteed to
 * be the latest frame received by that producer if the producer has set `buffered` to `false`.
 * Returns NULL and sets `error` on failure. */
lightning_message_t *lightning_consume(lightning_consumer_t *consumer, lightning_error_t *error);

/* Replies to the frame most recently returned by lightning_consume(), which returns
 * its token to the producer. Each frame should generate exactly one reply. Multiple calls
 * for a single lightning_consume() or a call without first calling consume will return
 * LIGHTNING_ERR_INVALID. */
lightning_error_t lightning_reply(lightning_consumer_t *consumer, const uint8_t *data,
        uint64_t data_size);

/* Cleanup methods. */
void lightning_free_message(lightning_message_t *message);
void lightning_free_producer(lightning_producer_t *producer);
void lightning_free_consumer(lightning_consumer_t *consumer);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* LIGHTNING_H */
