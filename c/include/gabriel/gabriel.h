#ifndef GABRIEL_H
#define GABRIEL_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Maximum size for a name. */
#define GABRIEL_MAX_NAME 50

/* Maximum consumers for a single producer, which sizes the producer's shared
 * memory pool for unbuffered connections. */
#define GABRIEL_MAX_CONSUMERS 32

/* Default max message size. */
#define GABRIEL_DEFAULT_MAX_MSG_SIZE 4000u /* 4 kilobytes */

/* Maximum number of tokens a producer can have. */
#define GABRIEL_MAX_TOKENS 100u

/* --- Declarations --- */

/* Log levels for log callback. */
typedef enum {
    GABRIEL_LOG_DEBUG,
    GABRIEL_LOG_INFO,
    GABRIEL_LOG_WARN,
    GABRIEL_LOG_ERROR,
} gabriel_log_level_t;

/* Log callback function declaration. */
typedef void (*gabriel_log_fn)(gabriel_log_level_t level,
        const char *message, void *user_data);

/* Error messages for gabriel protocol functions. */
typedef enum {
    GABRIEL_OK = 0,            /* no error */
    GABRIEL_ERR_DROPPED,       /* not enough tokens to send, backpressure signal */
    GABRIEL_ERR_SIZE_EXCEEDED, /* data_size is too large for producer max_send_size */
    GABRIEL_ERR_BROKEN_PIPE,   /* peer is disconnected */
    GABRIEL_ERR_INVALID,       /* bad argument */
    GABRIEL_ERR_CONNECTING,    /* socket is still connecting */
    GABRIEL_ERR_FULL,          /* at GABRIEL_MAX_CONSUMERS consumers */
    GABRIEL_ERR_INTERNAL,      /* internal failure */
    GABRIEL_ERR_AGAIN,         /* nothing available right now, try again */
} gabriel_error_t;

/* Gabriel protocol producer operating modes. */
typedef enum {
    GABRIEL_PRODUCER_MODE_FANOUT = 0,   /* fan out data to all consumers */
    GABRIEL_PRODUCER_MODE_SEQUENTIAL,   /* send data to consumers sequentially */
} gabriel_producer_mode_t;

/* Gabriel protocol token types. */
typedef enum {
    GABRIEL_TOKEN_NONE = 0,   /* no token */
    GABRIEL_TOKEN_ONE,        /* one token */
    GABRIEL_TOKEN_SKIP,       /* frame was skipped, provides one token */
    GABRIEL_TOKEN_ERR,        /* frame encountered an error, provides one token */
    GABRIEL_TOKEN_DROP,       /* frame was dropped, provides one token */
} gabriel_token_t;

/* Basic message exchange type. */
typedef struct gabriel_message_t {
    uint64_t seq_num;               /* frame sequence number, strictly increasing */
    char source[GABRIEL_MAX_NAME];  /* message source, set by producers and consumers */
    gabriel_token_t token;          /* token sent back by consumer */
    uint64_t offset;                /* offset pointer into the shared memory chunk, if applicable */
    uint8_t *metadata;              /* pointer to metadata (usually a result) */
    uint64_t metadata_size;         /* size of metadata array */
    uint8_t *data;                  /* pointer to data */
    uint64_t data_size;             /* size of data array */
} gabriel_message_t;

/* GabrielSource, GabrielEngine, GabrielSink, GabrielServer and
 * GabrielClient are the high-level types built on the internal
 * protocol layer. Opaque: callers only ever hold a pointer. */
typedef struct GabrielSource GabrielSource;
typedef struct GabrielEngine GabrielEngine;
typedef struct GabrielSink GabrielSink;
typedef struct GabrielServer GabrielServer;
typedef struct GabrielClient GabrielClient;

/* --- API --- */

/* Returns the library version string. */
const char *gabriel_version(void);

/* Sets the log callback for all gabriel protocol functions. This is not
 * threadsafe with other gabriel protocol calls, and should be set
 * *before* calling any other gabriel protocol functions. */
void gabriel_set_log_callback(gabriel_log_fn fn, void *user_cb);

/* TODO: these are stubs - arguments and real behavior aren't wired up
 * yet. */
GabrielSource *gabriel_new_source(void);
GabrielEngine *gabriel_new_engine(void);
GabrielSink *gabriel_new_sink(void);
GabrielServer *gabriel_new_server(void);
GabrielClient *gabriel_new_client(void);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GABRIEL_H */
