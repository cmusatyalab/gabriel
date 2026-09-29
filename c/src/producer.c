/* Gabriel producer implementation. */

#define _GNU_SOURCE /* needed for memfd_create */
#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/uio.h>
#include <sys/un.h>
#include <unistd.h>

#include "common.h"
#include "constants.h"
#include "gabriel_protocol.h"

/* Holds data for a single shared memory chunk. Shared memory is block allocated at
 * startup to have `max_tokens` chunks. These are handed out according to latest
 * sequence number for each consumer. */
typedef struct gabriel_chunk_data_t {
    _Atomic uint32_t refs;
    uint64_t seq_num;
    uint64_t data_size; /* bytes of payload actually valid in this chunk */
} gabriel_chunk_data_t;

/* Holds all data relevant to a consumer. */
typedef struct gabriel_consumer_data_t {
    pthread_rwlock_t rw_mu;
    const char *address;
    pthread_t connection_thread_id;
    bool waiting;
    bool connecting;
    bool shm;
    int family;
    union {
        struct sockaddr_un unet;
        struct sockaddr_in inet;
    } sock_attr;
    int fd;
    gabriel_chunk_data_t *held_chunk;
    /* Owning producer, so the connect thread can reach max_send_size,
     * chunk_size and shm_fd for the handshake. */
    gabriel_producer_t *producer;
} gabriel_consumer_data_t;

struct gabriel_producer_t {
    const char *name;
    _Atomic uint32_t num_tokens;
    _Atomic uint64_t seq_num;
    uint32_t max_tokens;
    uint64_t max_send_size;
    gabriel_producer_mode_t mode;
    pthread_rwlock_t consumers_rw_mu;
    _Atomic uint32_t rr_send_index;
    _Atomic uint32_t rr_reply_index;
    uint32_t consumer_count;
    gabriel_consumer_data_t *consumers[GABRIEL_MAX_CONSUMERS];
    int shm_fd;
    uint8_t *shm_base; /* mmap'd base of shm_fd, once mapped */
    uint32_t num_chunks;
    uint64_t chunk_size;
    gabriel_chunk_data_t *chunks;
};

/* Frees a gabriel_consumer_data_t object. */
static void gabriel_free_consumer_data(gabriel_consumer_data_t *consumer_data) {
    pthread_rwlock_wrlock(&consumer_data->rw_mu);
    if (consumer_data->connecting) { /* need to safely destroy the connection thread */
        pthread_cancel(consumer_data->connection_thread_id);
        pthread_join(consumer_data->connection_thread_id, NULL);
    }
    if (consumer_data->held_chunk != NULL) { /* need to decrement reference to held chunk */
        atomic_fetch_sub(&consumer_data->held_chunk->refs, 1);
    }
    if (consumer_data->fd >= 0) {
        close(consumer_data->fd);
    }
    pthread_rwlock_unlock(&consumer_data->rw_mu);
    pthread_rwlock_destroy(&consumer_data->rw_mu);
    free(consumer_data);
}

/* Performs the producer's half of the connection handshake on a
 * newly-connected `sockfd`. */
static bool handshake_with_consumer(int sockfd, gabriel_consumer_data_t *c) {
    gabriel_producer_t *p = c->producer;
    uint8_t is_shm = c->shm ? 1 : 0;
    uint64_t size_be = htobe64(c->shm ? p->chunk_size : p->max_send_size);

    if (!send_all(sockfd, &is_shm, sizeof(is_shm)) ||
            !send_all(sockfd, &size_be, sizeof(size_be))) {
        return false;
    }
    if (c->shm && !send_fd(sockfd, p->shm_fd)) {
        return false;
    }
    uint8_t ack = 0;
    if (!recv_all(sockfd, &ack, sizeof(ack))) {
        return false;
    }
    return ack == 1;
}

/* Applies a receive timeout to `fd`, so a stalled peer can't block a
 * blocking recv(). */
static void set_recv_timeout(int fd) {
    struct timeval tv = {.tv_sec = GABRIEL_RECV_TIMEOUT_S, .tv_usec = 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}

/* Closes the fd pointed to by `arg` if the thread is cancelled while
 * it's mid-connect, so an in-progress socket that never made it into
 * c->fd doesn't leak. */
static void close_fd_on_cancel(void *arg) {
    int fd = *(int *)arg;
    if (fd >= 0) {
        close(fd);
    }
}

/* Thread for connecting (and retrying connection) to a consumer address. */
static void *connect_to_consumer_thread(void *consumer) {
    gabriel_consumer_data_t *c = (gabriel_consumer_data_t *)consumer;
    /* Copy/write relevant data under the lock so the connect thread doesn't need to
     * hold the lock while connecting */
    pthread_rwlock_wrlock(&c->rw_mu);
    if (c->fd >= 0) { /* cleanup existing fd */
        close(c->fd);
        c->fd = -1;
    }
    c->waiting = false;
    c->connecting = true;
    int family = c->family;
    struct sockaddr_un unet = c->sock_attr.unet;
    struct sockaddr_in inet = c->sock_attr.inet;
    struct sockaddr *addr =
        family == AF_UNIX ? (struct sockaddr *)&unet : (struct sockaddr *)&inet;
    socklen_t addrlen = family == AF_UNIX ? sizeof(unet) : sizeof(inet);
    char name[GABRIEL_MAX_NAME];
    snprintf(name, sizeof(name), "%s", c->address); /* bounded by dest size, always terminated */
    pthread_rwlock_unlock(&c->rw_mu);
    int sleep_time = GABRIEL_BACKOFF_STEP_S;
    int sockfd = -1;
    do {
        sockfd = socket(family, SOCK_STREAM, 0);
        if (sockfd >= 0) {
            bool ok;
            pthread_cleanup_push(close_fd_on_cancel, &sockfd); /* set cleanup handler for fd */
            ok = !connect(sockfd, addr, addrlen);
            if (ok) {
                set_recv_timeout(sockfd);
                ok = handshake_with_consumer(sockfd, c);
            }
            pthread_cleanup_pop(0);
            if (ok) {
                break;
            }
        }
        close(sockfd);
        sleep(sleep_time);
        sleep_time = sleep_time * GABRIEL_BACKOFF_STEP_S;
        sleep_time = sleep_time > GABRIEL_BACKOFF_MAX_S ? GABRIEL_BACKOFF_MAX_S : sleep_time;
        gabriel_log(GABRIEL_LOG_WARN,
                  "gabriel: failed to connect to consumer %s, retrying in %d seconds",
                  name, sleep_time);
        continue;
    } while (true);
success:
    pthread_rwlock_wrlock(&c->rw_mu);
    c->fd = sockfd;
    c->connecting = false;
    c->waiting = true;
    pthread_rwlock_unlock(&c->rw_mu);
    pthread_detach(pthread_self());
    return NULL;
}

/* Starts (or restarts, for reconnection) the connection thread. */
static int connect_to_consumer(gabriel_consumer_data_t *c) {
    pthread_rwlock_wrlock(&c->rw_mu);
    if (pthread_create(&c->connection_thread_id, NULL, connect_to_consumer_thread, (void *)c) != 0) {
        gabriel_log(GABRIEL_LOG_ERROR,
                "gabriel: failed to start connect thread for %s", c->address);
        pthread_rwlock_unlock(&c->rw_mu);
        return -1;
    }
    pthread_rwlock_unlock(&c->rw_mu);
    return 0;
}

/* Checks the status of a consumer, and whether a reply is ready. 
 * Returns 0 if data is ready, -1 if the pipe is broken, and 1
 * if there is no data ready. */
static int peek_consumer(int fd) {
    uint8_t byte;
    for (;;) {
        ssize_t n = recv(fd, &byte, 1, MSG_PEEK | MSG_DONTWAIT);
        if (n > 0) {
            return 0; /* data to read */
        }
        if (n == 0) {
            return -1; /* peer performed an orderly shutdown */
        }
        if (errno == EINTR) {
            continue;
        }
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return 1; /* no data */
        }
        return -1; /* broken pipe */
    }
}

/* Searches for the first free chunk available (has zero refs). Returns
 * NULL if no chunk is available. */
static gabriel_chunk_data_t *get_free_chunk(gabriel_producer_t *producer) {
    for (uint32_t i = 0; i < producer->num_chunks; i++) {
        gabriel_chunk_data_t *current = producer->chunks + i;
        int expected = 0; /* have to redefine every time so it doesn't take on value of current->refs */
        /* Set the refs to 1 now that this is reserved */
        if (atomic_compare_exchange_strong(&current->refs, &expected, 1)) {
            return current;
        }
    }
    return NULL;
}

gabriel_producer_t *gabriel_new_producer(const char *name, uint32_t max_tokens,
        uint64_t max_send_size, gabriel_producer_mode_t mode, gabriel_error_t *error) {
    set_error(error, GABRIEL_OK);
    if (max_tokens > GABRIEL_MAX_TOKENS) {
        set_error(error, GABRIEL_ERR_INVALID);
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: max_tokens of %d exceeds MAX_TOKENS: %d", max_tokens,
                  GABRIEL_MAX_TOKENS);
        return NULL;
    }
    if (name != NULL && strlen(name) >= GABRIEL_MAX_NAME) {
        set_error(error, GABRIEL_ERR_INVALID);
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: name is too long (max %d characters)", GABRIEL_MAX_NAME - 1);
        return NULL;
    }
    gabriel_producer_t *p = calloc(1, sizeof(gabriel_producer_t));
    if (p == NULL) {
        set_error(error, GABRIEL_ERR_INTERNAL);
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed calloc for producer");
        return NULL;
    }
    p->name = name == NULL ? "" : name;
    atomic_store(&p->num_tokens, max_tokens != 0 ? 1 : 0);
    p->max_tokens = max_tokens;
    p->max_send_size = max_send_size != 0 ? max_send_size : GABRIEL_DEFAULT_MAX_MSG_SIZE;
    p->mode = mode;
    if (pthread_rwlock_init(&p->consumers_rw_mu, NULL) != 0) {
        set_error(error, GABRIEL_ERR_INTERNAL);
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed allocation of rwlock");
        return NULL;
    }
    p->shm_fd = memfd_create("shared_memory", 0);
    if (p->shm_fd < 0) {
        set_error(error, GABRIEL_ERR_INTERNAL);
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed creation of shm file descriptor");
        return NULL;
    }
    p->num_chunks = max_tokens != 0 ? max_tokens : GABRIEL_DEFAULT_CHUNK_COUNT;
    p->chunk_size = p->max_send_size + sizeof(gabriel_message_t);
    ftruncate(p->shm_fd, p->chunk_size * p->num_chunks);
    return p;
}

gabriel_error_t gabriel_add_consumer_to_producer(gabriel_producer_t *producer,
        const char *consumer_address) {
    if (consumer_address == NULL || strlen(consumer_address) >= GABRIEL_MAX_NAME) {
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: consumer address is missing or too long (max %d characters)",
                  GABRIEL_MAX_NAME - 1);
        return GABRIEL_ERR_INVALID;
    }
    pthread_rwlock_wrlock(&producer->consumers_rw_mu);
    gabriel_error_t err = GABRIEL_OK;
    if (producer->consumer_count >= GABRIEL_MAX_CONSUMERS) {
        err = GABRIEL_ERR_FULL;
        goto failure;
    }
    gabriel_consumer_data_t *c = calloc(1, sizeof(gabriel_consumer_data_t));
    if (c == NULL) {
        err = GABRIEL_ERR_INTERNAL;
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed allocation of consumer data");
        goto failure;
    }
    if (pthread_rwlock_init(&c->rw_mu, NULL) != 0) {
        err = GABRIEL_ERR_INTERNAL;
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed allocation of rwlock");
        goto failure;
    }
    c->address = consumer_address;
    c->producer = producer;
    if (!strncmp(consumer_address, GABRIEL_UNIX_PREFIX, 7) ||
            !strncmp(consumer_address, GABRIEL_SHM_PREFIX, 6)) { /* Unix socket */
        c->shm = !strncmp(consumer_address, GABRIEL_SHM_PREFIX, 6);
        int prefix_len = c->shm ? 6 : 7; /* the joke writes itself */
        strncpy(c->sock_attr.unet.sun_path, consumer_address + prefix_len,
                sizeof(c->sock_attr.unet.sun_path) - 1);
        c->family = AF_UNIX;
        if (c->shm && producer->chunks == NULL) { /* time to allocate shared memory */
            size_t total_size = (size_t)producer->chunk_size * producer->num_chunks;
            void *base = mmap(NULL, total_size, PROT_READ | PROT_WRITE, MAP_SHARED,
                    producer->shm_fd, 0);
            if (base == MAP_FAILED) {
                err = GABRIEL_ERR_INTERNAL;
                gabriel_log(GABRIEL_LOG_ERROR,
                        "gabriel: failed to map producer shared memory: %s",
                        strerror(errno));
                goto failure;
            }
            producer->chunks = calloc(producer->num_chunks, sizeof(gabriel_chunk_data_t));
            if (producer->chunks == NULL) {
                munmap(base, total_size);
                err = GABRIEL_ERR_INTERNAL;
                gabriel_log(GABRIEL_LOG_ERROR,
                        "gabriel: failed allocation of chunk bookkeeping");
                goto failure;
            }
            producer->shm_base = base;
        }
        if (connect_to_consumer(c) != 0) {
            err = GABRIEL_ERR_INTERNAL;
            gabriel_log(GABRIEL_LOG_ERROR, "gabriel: failed to connect to %s",
                    consumer_address);
            goto failure;
        }
    } else if (!strncmp(consumer_address, GABRIEL_TCP_PREFIX, 6)) { /* TCP socket */
        char ip[64];
        int port = 0;
        int parsed_items = sscanf(consumer_address, "tcp://%63[^:]:%d", ip, &port);
        if (parsed_items != 2) {
            err = GABRIEL_ERR_INVALID;
            gabriel_log(GABRIEL_LOG_ERROR,
                    "gabriel: address %s is incorrectly formatted", ip);
            goto failure;
        }
        c->sock_attr.inet.sin_family = AF_INET;
        c->sock_attr.inet.sin_port = htons(port);
        if (inet_pton(AF_INET, ip, &c->sock_attr.inet.sin_addr) <= 0) {
            err = GABRIEL_ERR_INVALID;
            gabriel_log(GABRIEL_LOG_ERROR, "gabriel: address %s is not valid", ip);
            goto failure;
        }
        c->family = AF_INET;
        if (connect_to_consumer(c) != 0) {
            err = GABRIEL_ERR_INTERNAL;
            gabriel_log(GABRIEL_LOG_ERROR, "gabriel: failed to connect to %s",
                    consumer_address);
            goto failure;
        }
    } else { /* unknown socket prefix */
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: consumer address %s has unknown prefix type",
                  consumer_address);
        err = GABRIEL_ERR_INVALID;
        goto failure;
    }
    producer->consumers[producer->consumer_count++] = c;
    pthread_rwlock_unlock(&producer->consumers_rw_mu);
    return err;
failure:
    free(c);
    pthread_rwlock_unlock(&producer->consumers_rw_mu);
    return err;
}

gabriel_error_t gabriel_remove_consumer_from_producer(gabriel_producer_t *producer,
        const char *consumer_address) {
    gabriel_error_t err = GABRIEL_OK;
    pthread_rwlock_wrlock(&producer->consumers_rw_mu);
    bool found = false;
    for (int i = 0; i < producer->consumer_count; i++) {
        pthread_rwlock_rdlock(&producer->consumers[i]->rw_mu);
        if (!strncmp(producer->consumers[i]->address, consumer_address, strlen(consumer_address))) {
            pthread_rwlock_unlock(&producer->consumers[i]->rw_mu);
            gabriel_free_consumer_data(producer->consumers[i]);
            memmove(&producer->consumers[i], &producer->consumers[i + 1],
                    producer->consumer_count - i - 1); /* shift elements of array to fill the gap */
            producer->consumer_count--;
            /* Adjust indices to reflect new consumer count. */
            if (producer->consumer_count > 0) {
                atomic_store(&producer->rr_send_index,
                        producer->rr_send_index % producer->consumer_count);
                atomic_store(&producer->rr_reply_index,
                        producer->rr_reply_index % producer->consumer_count);
            } else {
                atomic_store(&producer->rr_send_index, 0);
                atomic_store(&producer->rr_reply_index, 0);
            }
            found = true;
            break;
        } else {
            pthread_rwlock_unlock(&producer->consumers[i]->rw_mu);
        }
    }
    if (!found) {
        err = GABRIEL_ERR_INVALID;
    }
    pthread_rwlock_unlock(&producer->consumers_rw_mu);
    return err;
}

gabriel_error_t gabriel_produce(gabriel_producer_t *producer, const uint8_t *metadata,
        uint64_t metadata_size, const uint8_t *data, uint64_t data_size, uint64_t seq_num) {
    gabriel_error_t err = GABRIEL_OK;
    gabriel_message_t message; /* message to send over the wire */
    snprintf(message.source, GABRIEL_MAX_NAME, "%s", producer->name); /* bounded, always terminated */
    message.metadata = metadata;
    message.metadata_size = metadata_size;
    message.data = data;
    message.data_size = data_size;
    message.seq_num = seq_num;
    message.offset = 0;
    message.token = GABRIEL_TOKEN_NONE;
    gabriel_chunk_data_t *free_chunk = NULL; /* used for shm connections */
    uint32_t chunk_index = 0;

    pthread_rwlock_rdlock(&producer->consumers_rw_mu);
    if (data_size > producer->max_send_size) { /* check that data is within size bounds */
        err = GABRIEL_ERR_SIZE_EXCEEDED;
        gabriel_log(GABRIEL_LOG_ERROR,
                "gabriel: data_size %llu exceeds max_send_size %llu",
                (unsigned long long)data_size, (unsigned long long)producer->max_send_size);
        goto end;
    } else if (producer->consumer_count == 0) {
        err = GABRIEL_ERR_DROPPED;
        gabriel_log(GABRIEL_LOG_ERROR, "gabriel: no consumers attached to producer");
        goto end;
    } else if (producer->max_tokens != 0 && atomic_load(&producer->num_tokens) == 0) { /* check tokens */
        err = GABRIEL_ERR_DROPPED;
        gabriel_log(GABRIEL_LOG_ERROR, "gabriel: producer has no tokens");
        goto end;
    }
    if (producer->chunks != NULL) { /* at least one shm consumer exists */
        free_chunk = get_free_chunk(producer);
        if (free_chunk == NULL) {
            err = GABRIEL_ERR_DROPPED;
            gabriel_log(GABRIEL_LOG_ERROR, "gabriel: producer has no free shared memory chunks");
            goto end;
        }
        chunk_index = (uint32_t)(free_chunk - producer->chunks);
        free_chunk->seq_num = seq_num;
        free_chunk->data_size = data_size;
        message.offset = (uint64_t)chunk_index * producer->chunk_size;
        uint8_t *membase = producer->shm_base + message.offset;
        if (data_size > 0) { /* copy data into shm chunk */
            memcpy(membase, data, data_size);
        }
    }

    if (producer->mode == GABRIEL_PRODUCER_MODE_FANOUT) { /* iterate over all consumers */
        int sent = 0;
        int shm_recipients = 0;
        for (uint32_t i = 0; i < producer->consumer_count; i++) {
            gabriel_consumer_data_t *c = producer->consumers[i];
            pthread_rwlock_rdlock(&c->rw_mu);
            bool waiting = c->waiting;
            int fd = c->fd;
            pthread_rwlock_unlock(&c->rw_mu);
            if (!waiting) {
                continue; /* not connected yet: skip, don't count against this send */
            }
            gabriel_error_t send_err = send_message(fd, &message);
            if (send_err == GABRIEL_OK) {
                sent++;
                if (c->shm) {
                    shm_recipients++;
                }
            }
        }
        if (sent == 0) {
            err = GABRIEL_ERR_DROPPED;
        }
        if (free_chunk != NULL) { /* set ref count for free chunk */
            atomic_store(&free_chunk->refs, shm_recipients);
        }
    } else { /* only send to the next consumer */
        uint32_t index = atomic_fetch_add(&producer->rr_send_index, 1) % producer->consumer_count;
        gabriel_consumer_data_t *c = producer->consumers[index];
        pthread_rwlock_rdlock(&c->rw_mu);
        bool waiting = c->waiting;
        int fd = c->fd;
        pthread_rwlock_unlock(&c->rw_mu);
        if (!waiting) {
            err = GABRIEL_ERR_CONNECTING;
        } else {
            err = send_message(fd, &message);
            if (err == GABRIEL_OK && c->shm && free_chunk != NULL) {
                atomic_store(&free_chunk->refs, 1);
            } else if (err != GABRIEL_OK) { /* sequential producers reset on error */
                atomic_store(&producer->rr_send_index, 0);
                atomic_store(&producer->rr_reply_index, 0);
            }
        }
    }
end:
    pthread_rwlock_unlock(&producer->consumers_rw_mu);
    return err;
}

gabriel_message_t *gabriel_read_reply(gabriel_producer_t *producer, gabriel_error_t *error) {
    set_error(error, GABRIEL_OK);
    gabriel_message_t *message = NULL;
    pthread_rwlock_rdlock(&producer->consumers_rw_mu);

    if (producer->consumer_count == 0) {
        pthread_rwlock_unlock(&producer->consumers_rw_mu);
        set_error(error, GABRIEL_ERR_INVALID);
        return NULL;
    }

    if (producer->mode == GABRIEL_PRODUCER_MODE_FANOUT) {
        /* Search forward from rr_reply_index for a waiting consumer. */
        uint32_t count = producer->consumer_count;
        uint32_t start = atomic_load(&producer->rr_reply_index) % count;
        for (uint32_t k = 0; k < count; k++) {
            uint32_t i = (start + k) % count;
            gabriel_consumer_data_t *c = producer->consumers[i];

            pthread_rwlock_rdlock(&c->rw_mu);
            bool waiting = c->waiting;
            int fd = c->fd;
            pthread_rwlock_unlock(&c->rw_mu);
            if (!waiting) {
                continue; /* keep searching forward */
            }

            int peek = peek_consumer(fd);
            atomic_store(&producer->rr_reply_index, (i + 1) % count);
            if (peek == 1) { /* no data */
                pthread_rwlock_unlock(&producer->consumers_rw_mu);
                set_error(error, GABRIEL_ERR_AGAIN);
                return NULL;
            }
            if (peek == -1) { /* broken: reconnect and move on, same as no data */
                connect_to_consumer(c);
                pthread_rwlock_unlock(&producer->consumers_rw_mu);
                set_error(error, GABRIEL_ERR_AGAIN);
                return NULL;
            }
            message = recv_message(fd, error);
            pthread_rwlock_unlock(&producer->consumers_rw_mu);
            return message;
        }
        /* No consumer is currently waiting; nothing was actually
         * examined, so leave rr_reply_index where it is */
        set_error(error, GABRIEL_ERR_CONNECTING);
    } else {
        /* Sequential producers only ever look at the current index, no searching */
        uint32_t i = atomic_load(&producer->rr_reply_index) % producer->consumer_count;
        gabriel_consumer_data_t *c = producer->consumers[i];
        pthread_rwlock_rdlock(&c->rw_mu);
        bool waiting = c->waiting;
        int fd = c->fd;
        pthread_rwlock_unlock(&c->rw_mu);
        if (!waiting) {
            pthread_rwlock_unlock(&producer->consumers_rw_mu);
            set_error(error, GABRIEL_ERR_CONNECTING);
            return NULL;
        }

        int peek = peek_consumer(fd);
        if (peek == 1) { /* no data */
            pthread_rwlock_unlock(&producer->consumers_rw_mu);
            set_error(error, GABRIEL_ERR_AGAIN);
            return NULL;
        }
        if (peek == -1) { /* broken: reconnect, and unlike fanout, reset to the start */
            connect_to_consumer(c);
            atomic_store(&producer->rr_reply_index, 0);
            pthread_rwlock_unlock(&producer->consumers_rw_mu);
            set_error(error, GABRIEL_ERR_INTERNAL);
            return NULL;
        }
        message = recv_message(fd, error);
    }

    pthread_rwlock_unlock(&producer->consumers_rw_mu);
    return message;
}

void gabriel_free_producer(gabriel_producer_t *producer) {
    if (producer == NULL) {
        return;
    }
    pthread_rwlock_wrlock(&producer->consumers_rw_mu);
    for (uint32_t i = 0; i < producer->consumer_count; i++) {
        gabriel_free_consumer_data(producer->consumers[i]);
    }
    producer->consumer_count = 0;
    if (producer->shm_base != NULL) {
        munmap(producer->shm_base, (size_t)producer->chunk_size * producer->num_chunks);
    }
    free(producer->chunks);
    if (producer->shm_fd >= 0) {
        close(producer->shm_fd);
    }
    pthread_rwlock_unlock(&producer->consumers_rw_mu);
    pthread_rwlock_destroy(&producer->consumers_rw_mu);
    free(producer);
}
