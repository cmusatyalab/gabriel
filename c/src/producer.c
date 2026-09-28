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
    atomic_int refs;
    uint64_t seq_num;
    uint64_t data_size; /* bytes of payload actually valid in this chunk */
} gabriel_chunk_data_t;

/* Holds all data relevant to a consumer. */
typedef struct gabriel_consumer_data_t {
    pthread_rwlock_t consumer_data_rw_mu;
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
    atomic_int num_tokens;
    uint32_t max_tokens;
    uint64_t max_send_size;
    gabriel_producer_mode_t mode;
    pthread_rwlock_t consumer_rw_mu;
    uint32_t rr_send_index;
    uint32_t rr_reply_index;
    uint32_t consumer_count;
    gabriel_consumer_data_t *consumers[GABRIEL_MAX_CONSUMERS];
    int shm_fd;
    uint8_t *shm_base; /* mmap'd base of shm_fd, once mapped */
    uint32_t num_chunks;
    uint64_t chunk_size;
    gabriel_chunk_data_t *chunks;
};

/* Frees a gabriel_consumer_data_t object. Write lock for consumers must be held
 * to safely call. */
static void gabriel_free_consumer_data(gabriel_consumer_data_t *consumer_data) {
    pthread_rwlock_wrlock(&consumer_data->consumer_data_rw_mu);
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
    pthread_rwlock_unlock(&consumer_data->consumer_data_rw_mu);
    pthread_rwlock_destroy(&consumer_data->consumer_data_rw_mu);
    free(consumer_data);
}

/* Performs the producer's half of the connection handshake on a
 * freshly-connected `sockfd`. */
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

/* Thread for connecting (and retrying connection) to a consumer address. */
static void *connect_to_consumer(void *consumer) {
    gabriel_consumer_data_t *c = (gabriel_consumer_data_t *)consumer;
    /* Copy relevant data under the lock so the connect thread doesn't need to
     * hold the lock while connecting */
    pthread_rwlock_rdlock(&c->consumer_data_rw_mu);
    int family = c->family;
    struct sockaddr_un unet = c->sock_attr.unet;
    struct sockaddr_in inet = c->sock_attr.inet;
    struct sockaddr *addr =
        family == AF_UNIX ? (struct sockaddr *)&unet : (struct sockaddr *)&inet;
    socklen_t addrlen = family == AF_UNIX ? sizeof(unet) : sizeof(inet);
    char name[GABRIEL_MAX_NAME];
    strncpy(name, c->address, strlen(c->address));
    pthread_rwlock_unlock(&c->consumer_data_rw_mu);
    int sleep_time = GABRIEL_BACKOFF_STEP_S;
    int sockfd = -1;
    do {
        sockfd = socket(family, SOCK_STREAM, 0);
        if (sockfd >= 0 && !connect(sockfd, addr, addrlen) &&
                handshake_with_consumer(sockfd, c)) {
            break;
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
    pthread_rwlock_wrlock(&c->consumer_data_rw_mu);
    c->fd = sockfd;
    c->connecting = false;
    c->waiting = true;
    pthread_rwlock_unlock(&c->consumer_data_rw_mu);
    return NULL;
}

/* Checks the status of a consumer, and whether a reply is ready. */
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

/* `c`'s connection was found broken: closes the old fd, marks it as
 * connecting again, and spawns a fresh connect thread to reestablish
 * it (reusing connect_to_consumer(), which already owns the retry/
 * backoff loop). Requires `c`'s own lock to not be held. */
static void reconnect_consumer(gabriel_consumer_data_t *c) {
    pthread_rwlock_wrlock(&c->consumer_data_rw_mu);
    if (c->fd >= 0) {
        close(c->fd);
        c->fd = -1;
    }
    c->waiting = false;
    c->connecting = true;
    pthread_rwlock_unlock(&c->consumer_data_rw_mu);
    if (pthread_create(&c->connection_thread_id, NULL, connect_to_consumer, (void *)c) != 0) {
        gabriel_log(GABRIEL_LOG_ERROR,
                "gabriel: failed to start reconnect thread for %s", c->address);
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

/* Sends one produced frame to `c`. For an shm consumer, only the
 * notification (seq_num/offset/data_size) goes over the wire; the
 * payload already lives in `chunk` at byte offset
 * `chunk_index * chunk_size` in the producer's shared memory, which
 * the consumer mapped during the handshake. For a non-shm consumer,
 * the raw payload follows the notification directly on the same
 * connection ("consumers always send/receive buffered over the
 * wire" - only frame delivery to shm consumers is zero-copy).
 *
 * On success for an shm consumer, records that `c` now holds `chunk`
 * until its next reply, releasing whatever chunk it held before
 * (an unreplied frame is superseded by a newer one, per the
 * newest-wins design). */
static gabriel_error_t send_frame_to_consumer(gabriel_consumer_data_t *c,
        gabriel_chunk_data_t *chunk, uint32_t chunk_index, uint64_t chunk_size,
        const uint8_t *data, uint64_t data_size, uint64_t seq_num) {
    pthread_rwlock_rdlock(&c->consumer_data_rw_mu);
    bool waiting = c->waiting;
    int fd = c->fd;
    pthread_rwlock_unlock(&c->consumer_data_rw_mu);

    if (!waiting) {
        return GABRIEL_ERR_CONNECTING;
    }

    uint64_t offset = c->shm ? (uint64_t)chunk_index * chunk_size : 0;
    uint64_t fields[3] = {htobe64(seq_num), htobe64(offset), htobe64(data_size)};
    bool ok = send_all(fd, fields, sizeof(fields));
    if (ok && !c->shm && data_size > 0) {
        ok = send_all(fd, data, data_size);
    }
    if (!ok) {
        return GABRIEL_ERR_BROKEN_PIPE;
    }

    if (c->shm) {
        pthread_rwlock_wrlock(&c->consumer_data_rw_mu);
        if (c->held_chunk != NULL) {
            atomic_fetch_sub(&c->held_chunk->refs, 1); /* superseded: release the old hold */
        }
        c->held_chunk = chunk;
        pthread_rwlock_unlock(&c->consumer_data_rw_mu);
    }
    return GABRIEL_OK;
}

/* Reads and parses one reply already known to be waiting on `c->fd`
 * (`fd`), advancing/releasing state exactly as the single-consumer
 * version used to. Returns NULL and sets `*error` on failure. */
static gabriel_message_t *consume_reply(gabriel_producer_t *producer,
        gabriel_consumer_data_t *c, int fd, gabriel_error_t *error) {
    /* token(1) seq_num(8) offset(8) data_size(8) */
    uint8_t hdr[1 + 8 + 8 + 8];
    if (!recv_all(fd, hdr, sizeof(hdr))) {
        set_error(error, GABRIEL_ERR_BROKEN_PIPE);
        return NULL;
    }
    gabriel_token_t token = (gabriel_token_t)hdr[0];
    uint64_t seq_num_be, offset_be, data_size_be;
    memcpy(&seq_num_be, hdr + 1, 8);
    memcpy(&offset_be, hdr + 9, 8);
    memcpy(&data_size_be, hdr + 17, 8);
    uint64_t seq_num = be64toh(seq_num_be);
    uint64_t offset = be64toh(offset_be);
    uint64_t data_size = be64toh(data_size_be);

    /* Replies always arrive as raw bytes over the wire, shm or not. */
    uint8_t *data = NULL;
    if (data_size > 0) {
        data = malloc(data_size);
        if (data == NULL || !recv_all(fd, data, data_size)) {
            free(data);
            set_error(error, GABRIEL_ERR_BROKEN_PIPE);
            return NULL;
        }
    }

    /* This reply returns whatever chunk `c` was holding. */
    pthread_rwlock_wrlock(&c->consumer_data_rw_mu);
    if (c->held_chunk != NULL) {
        atomic_fetch_sub(&c->held_chunk->refs, 1);
        c->held_chunk = NULL;
    }
    pthread_rwlock_unlock(&c->consumer_data_rw_mu);

    /* AIMD: a DROP or ERR is a backpressure signal (multiplicative
     * decrease); anything else is a normal ack (additive increase,
     * capped at max_tokens). */
    if (producer->max_tokens != 0) {
        int cur = atomic_load(&producer->num_tokens);
        int next;
        do {
            if (token == GABRIEL_TOKEN_DROP || token == GABRIEL_TOKEN_ERR) {
                next = cur > 1 ? cur / 2 : 1;
            } else {
                next = cur < (int)producer->max_tokens ? cur + 1 : cur;
            }
        } while (!atomic_compare_exchange_weak(&producer->num_tokens, &cur, next));
    }

    gabriel_message_t *msg = calloc(1, sizeof(*msg));
    if (msg == NULL) {
        free(data);
        set_error(error, GABRIEL_ERR_INTERNAL);
        return NULL;
    }
    msg->seq_num = seq_num;
    strncpy(msg->source, c->address, GABRIEL_MAX_NAME - 1);
    msg->token = token;
    msg->offset = offset;
    msg->data = data;
    msg->data_size = data_size;
    return msg;
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
    gabriel_producer_t *p = calloc(1, sizeof(*p));
    if (p == NULL) {
        set_error(error, GABRIEL_ERR_INTERNAL);
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed calloc for producer");
        return NULL;
    }
    p->name = name == NULL ? "" : name;
    p->num_tokens = max_tokens != 0 ? 1 : 0;
    p->max_tokens = max_tokens;
    p->max_send_size = max_send_size != 0 ? max_send_size : GABRIEL_DEFAULT_MAX_MSG_SIZE;
    p->mode = mode;
    if (pthread_rwlock_init(&p->consumer_rw_mu, NULL) != 0) {
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
    pthread_rwlock_wrlock(&producer->consumer_rw_mu);
    gabriel_error_t err = GABRIEL_OK;
    if (producer->consumer_count >= GABRIEL_MAX_CONSUMERS) {
        err = GABRIEL_ERR_FULL;
        goto failure;
    }
    gabriel_consumer_data_t *c = calloc(1, sizeof(*c));
    if (c == NULL) {
        err = GABRIEL_ERR_INTERNAL;
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed allocation of consumer data");
        goto failure;
    }
    if (pthread_rwlock_init(&c->consumer_data_rw_mu, NULL) != 0) {
        err = GABRIEL_ERR_INTERNAL;
        gabriel_log(GABRIEL_LOG_ERROR,
                  "gabriel: failed allocation of rwlock");
        goto failure;
    }
    c->address = consumer_address;
    c->connecting = true;
    c->fd = -1;
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
            producer->chunks = calloc(producer->num_chunks, sizeof(*producer->chunks));
            if (producer->chunks == NULL) {
                munmap(base, total_size);
                err = GABRIEL_ERR_INTERNAL;
                gabriel_log(GABRIEL_LOG_ERROR,
                        "gabriel: failed allocation of chunk bookkeeping");
                goto failure;
            }
            producer->shm_base = base;
        }
        if (pthread_create(&c->connection_thread_id, NULL, connect_to_consumer, (void *)c) != 0) {
            err = GABRIEL_ERR_INTERNAL;
            gabriel_log(GABRIEL_LOG_ERROR, "gabriel: failed to start connect thread for %s",
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
        if (pthread_create(&c->connection_thread_id, NULL, connect_to_consumer, (void *)c) != 0) {
            err = GABRIEL_ERR_INTERNAL;
            gabriel_log(GABRIEL_LOG_ERROR, "gabriel: failed to start connect thread for %s",
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
    pthread_rwlock_unlock(&producer->consumer_rw_mu);
    return err;
failure:
    free(c);
    pthread_rwlock_unlock(&producer->consumer_rw_mu);
    return err;
}

gabriel_error_t gabriel_remove_consumer_from_producer(gabriel_producer_t *producer,
        const char *consumer_address) {
    gabriel_error_t err = GABRIEL_OK;
    pthread_rwlock_wrlock(&producer->consumer_rw_mu);
    bool found = false;
    for (int i = 0; i < producer->consumer_count; i++) {
        pthread_rwlock_rdlock(&producer->consumers[i]->consumer_data_rw_mu);
        if (!strncmp(producer->consumers[i]->address, consumer_address, strlen(consumer_address))) {
            pthread_rwlock_unlock(&producer->consumers[i]->consumer_data_rw_mu);
            gabriel_free_consumer_data(producer->consumers[i]);
            memmove(&producer->consumers[i], &producer->consumers[i + 1],
                    producer->consumer_count - i - 1); /* shift elements of array to fill the gap */
            producer->consumer_count--;
            /* Adjust indices to reflect new consumer count */
            producer->rr_send_index = producer->rr_send_index % producer->consumer_count;
            producer->rr_reply_index = producer->rr_reply_index % producer->consumer_count;
            found = true;
            break;
        } else {
            pthread_rwlock_unlock(&producer->consumers[i]->consumer_data_rw_mu);
        }
    }
    if (!found) {
        err = GABRIEL_ERR_INVALID;
    }
    pthread_rwlock_unlock(&producer->consumer_rw_mu);
    return err;
}

gabriel_error_t gabriel_produce(gabriel_producer_t *producer, const uint8_t *data,
        uint64_t data_size, uint64_t seq_num) {
    if (data_size > producer->max_send_size) {
        gabriel_log(GABRIEL_LOG_ERROR,
                "gabriel: data_size %llu exceeds max_send_size %llu",
                (unsigned long long)data_size, (unsigned long long)producer->max_send_size);
        return GABRIEL_ERR_SIZE_EXCEEDED;
    }

    gabriel_error_t err = GABRIEL_OK;
    gabriel_chunk_data_t *free_chunk = NULL;
    uint32_t chunk_index = 0;
    uint32_t shm_recipients = 0;

    pthread_rwlock_rdlock(&producer->consumer_rw_mu);
    if (producer->max_tokens != 0 && atomic_load(&producer->num_tokens) == 0) {
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
        if (data_size > 0) {
            memcpy(producer->shm_base + (uint64_t)chunk_index * producer->chunk_size,
                    data, data_size);
        }
    }

    if (producer->mode == GABRIEL_PRODUCER_MODE_FANOUT) { /* iterate over all consumers */
        uint32_t sent = 0;
        for (uint32_t i = 0; i < producer->consumer_count; i++) {
            gabriel_consumer_data_t *c = producer->consumers[i];
            gabriel_error_t send_err = send_frame_to_consumer(c, free_chunk, chunk_index,
                    producer->chunk_size, data, data_size, seq_num);
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
    } else { /* only send to the next consumer */
        if (producer->consumer_count == 0) {
            err = GABRIEL_ERR_DROPPED;
        } else {
            gabriel_consumer_data_t *c = producer->consumers[producer->rr_send_index];
            err = send_frame_to_consumer(c, free_chunk, chunk_index, producer->chunk_size,
                    data, data_size, seq_num);
            if (err == GABRIEL_OK && c->shm) {
                shm_recipients = 1;
            }
            producer->rr_send_index = (producer->rr_send_index + 1) % producer->consumer_count;
        }
    }

    if (free_chunk != NULL) {
        /* Releases the writer's temporary hold from get_free_chunk() and
         * sets the chunk's true reference count to however many shm
         * consumers now hold it - 0 if none, meaning it's immediately
         * reusable. */
        atomic_store(&free_chunk->refs, (int)shm_recipients);
    }
end:
    pthread_rwlock_unlock(&producer->consumer_rw_mu);
    return err;
}

gabriel_message_t *gabriel_read_reply(gabriel_producer_t *producer, gabriel_error_t *error) {
    set_error(error, GABRIEL_OK);

    pthread_rwlock_rdlock(&producer->consumer_rw_mu);
    uint32_t count = producer->consumer_count;
    if (count == 0) {
        pthread_rwlock_unlock(&producer->consumer_rw_mu);
        set_error(error, GABRIEL_ERR_INVALID);
        return NULL;
    }

    if (producer->mode == GABRIEL_PRODUCER_MODE_FANOUT) {
        /* Search forward from rr_reply_index for a waiting consumer. */
        for (uint32_t k = 0; k < count; k++) {
            uint32_t i = (producer->rr_reply_index + k) % count;
            gabriel_consumer_data_t *c = producer->consumers[i];

            pthread_rwlock_rdlock(&c->consumer_data_rw_mu);
            bool waiting = c->waiting;
            int fd = c->fd;
            pthread_rwlock_unlock(&c->consumer_data_rw_mu);
            if (!waiting) {
                continue;
            }

            peek_result_t peek = peek_consumer(fd);
            producer->rr_reply_index = (i + 1) % count;
            if (peek > 0) { /* no data */
                pthread_rwlock_unlock(&producer->consumer_rw_mu);
                set_error(error, GABRIEL_ERR_AGAIN);
                return NULL;
            }
            if (peek < 0) { /* broken pipe */
                reconnect_consumer(c);
                pthread_rwlock_unlock(&producer->consumer_rw_mu);
                set_error(error, GABRIEL_ERR_AGAIN);
                return NULL;
            }
            gabriel_message_t *msg = consume_reply(producer, c, fd, error);
            pthread_rwlock_unlock(&producer->consumer_rw_mu);
            return msg;
        }
        /* No consumer currently waiting; leave rr_reply_index where it
         * is since nothing was actually examined. */
        pthread_rwlock_unlock(&producer->consumer_rw_mu);
        set_error(error, GABRIEL_ERR_CONNECTING);
        return NULL;
    }

    /* Sequential: only ever look at the current index, no searching. */
    uint32_t i = producer->rr_reply_index % count;
    gabriel_consumer_data_t *c = producer->consumers[i];
    pthread_rwlock_rdlock(&c->consumer_data_rw_mu);
    bool waiting = c->waiting;
    int fd = c->fd;
    pthread_rwlock_unlock(&c->consumer_data_rw_mu);
    if (!waiting) {
        pthread_rwlock_unlock(&producer->consumer_rw_mu);
        set_error(error, GABRIEL_ERR_CONNECTING);
        return NULL;
    }

    peek_result_t peek = peek_consumer(fd);
    if (peek > 0) { /* no data */
        pthread_rwlock_unlock(&producer->consumer_rw_mu);
        set_error(error, GABRIEL_ERR_AGAIN);
        return NULL;
    }
    if (peek < 0) { /* broken pipe */
        reconnect_consumer(c);
        producer->rr_reply_index = 0;
        pthread_rwlock_unlock(&producer->consumer_rw_mu);
        set_error(error, GABRIEL_ERR_INTERNAL);
        return NULL;
    }
    gabriel_message_t *msg = consume_reply(producer, c, fd, error);
    pthread_rwlock_unlock(&producer->consumer_rw_mu);
    return msg;
}

void gabriel_free_producer(gabriel_producer_t *producer) {
    if (producer == NULL) {
        return;
    }
    pthread_rwlock_wrlock(&producer->consumer_rw_mu);
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
    pthread_rwlock_unlock(&producer->consumer_rw_mu);
    pthread_rwlock_destroy(&producer->consumer_rw_mu);
    free(producer);
}
