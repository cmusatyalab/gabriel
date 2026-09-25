/* Lightning producer implementation. */

#include <pthread.h>
#include <stdatomic.h>

#include "constants.h"
#include "common.h"
#include "lightning/lightning.h"

typedef struct consumer_connection_t {
    _Atomic int refs;
    int fd;
    bool shm;
    
} consumer_connection_t;

typedef struct consumer_data_t {
    lightning_address_t address;
    bool removed;
    bool connecting;
    consumer_connection_t *conn;

} consumer_data_t;

struct lightning_producer_t {
    bool token_gated;
    uint32_t num_tokens;
    uint32_t max_tokens;
    uint64_t max_send_size;
    pthread_mutex_t send_mu;
    pthread_mutex_t read_mu;
    pthread_mutex_t data_mu;

};


