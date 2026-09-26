/* Lightning producer implementation. */

#include <pthread.h>
#include <stdatomic.h>

#include "constants.h"
#include "common.h"
#include "lightning/lightning.h"

/* Holds data for a single consumer connection. */
typedef struct consumer_connection_t {
    int fd;
    bool shm;
    wire_writer_t writer;
    wire_reader_t reader;
} consumer_connection_t;

/* Holds all data relevant to a consumer. */
typedef struct consumer_data_t {
    pthread_mutex_t consumer_data_mu;
    lightning_address_t address;
    bool connecting;
    uint32_t *held_chunk;
    consumer_connection_t *conn;
} consumer_data_t;

struct lightning_producer_t {
    uint32_t num_tokens;
    uint32_t max_tokens;
    uint64_t max_send_size;
    pthread_rwlock_t consumer_rw_mu;
    uint32_t rr_reply_index;
    uint32_t consumer_count;
    consumer_data_t *consumers[LIGHTNING_MAX_CONSUMERS];

};

lightning_producer_t *lightning_create_producer(uint32_t max_tokens, uint64_t max_send_size,
        lightning_error_t *error) {
    set_error(error, LIGHTNING_OK);
    if (max_tokens > LIGHTNING_MAX_TOKENS) {
        set_error(error, LIGHTNING_ERR_INVALID);
        lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: max_tokens of %d exceeds MAX_TOKENS: %d", max_tokens,
                  LIGHTNING_MAX_TOKENS);
    }
    
    lightning_producer_t *p = calloc(1, sizeof(*p));
    if (p == NULL) {
        set_error(error, LIGHTNING_ERR_INTERNAL);
        lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: failed calloc for producer");
        return NULL;
    }
    p->max_tokens = max_tokens;
    p->max_send_size = max_send_size;
    p->latest = -1;
}
