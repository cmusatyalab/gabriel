#ifndef LIGHTNING_INTERNAL_H
#define LIGHTNING_INTERNAL_H

/* Helpers shared by producer.c and consumer.c: logging, addresses,
 * blocking socket I/O, shared memory, the wire protocol, the handshake
 * and message allocation. All of it lives in common.c. */

#include <netinet/in.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/socket.h>
#include <sys/un.h>

#include "lightning/lightning.h"

/* ---- Logging / errors ---- */

/* Formats a message and hands it to the callback registered with
 * lightning_set_log_callback(), if any. */
void lightning_log(lightning_log_level_t level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));

/* Sets *error to value if error is non-NULL. */
void set_error(lightning_error_t *error, lightning_error_t value);

/* ---- Addresses ---- */

/* Longest source_name / host_name we accept. */
#define LT_NAME_MAX 255

typedef struct {
  int family; /* AF_INET or AF_UNIX */
  bool shm;   /* "shm://" (unbuffered) rather than "unix://" / "tcp://" */
  socklen_t len;
  union {
    struct sockaddr_in in;
    struct sockaddr_un un;
  } sa;
} lt_addr_t;

/* Parses "tcp://ip:port", "unix://path" or "shm://path". Returns 0 on
 * success, -1 if malformed. */
int parse_address(const char *address, lt_addr_t *out);

/* Fills `out` with the peer's IPv4 address, or "" for a Unix socket. */
void peer_ip(int fd, char out[INET_ADDRSTRLEN]);

/* ---- Misc helpers ---- */

uint64_t now_ms(void);     /* monotonic clock, for retry scheduling */
uint64_t random_id(void);  /* nonzero random 64-bit ID */

/* Blocking send()/recv() of exactly `len` bytes, retrying on EINTR.
 * Return 0 on success, -1 on error or disconnection. */
int send_all(int fd, const void *buf, size_t len);
int recv_all(int fd, void *buf, size_t len);

/* Passes a file descriptor over a Unix socket (SCM_RIGHTS), and
 * receives one. recv_fd() returns -1 on failure. */
int send_fd(int sock, int pass_fd);
int recv_fd(int sock);

/* Applies a send/recv timeout to a socket (0 clears it). Used to bound
 * the handshake. */
void set_io_timeout(int fd, unsigned ms);

/* TCP_NODELAY plus the best available congestion control. */
void tcp_tune(int fd);

/* ---- Shared memory ----
 *
 * A pool is a memfd divided into `chunk_count` equal chunks, each big
 * enough for one frame. Only the producer writes to it; consumers map
 * it read-only. Pages only get real memory once they're written. */

typedef struct {
  int fd;
  uint8_t *base;
  uint32_t chunk_count;
  uint64_t stride; /* bytes per chunk */
  size_t size;
} lt_shm_t;

/* Creates and maps (read-write) a pool of `chunk_count` chunks that each
 * fit `max_data` bytes. Returns 0 on success. */
int shm_create(lt_shm_t *shm, uint32_t chunk_count, uint64_t max_data);

/* Maps (read-only) a pool received from the producer, after checking
 * the memfd is really as large as the layout claims. Takes ownership of
 * `fd`. Returns 0 on success. */
int shm_map(lt_shm_t *shm, int fd, uint32_t chunk_count, uint64_t stride);

/* Unmaps and closes a pool. Safe on a zeroed or already released one. */
void shm_release(lt_shm_t *shm);

static inline uint8_t *shm_chunk(const lt_shm_t *shm, uint32_t i) {
  return shm->base + (uint64_t)i * shm->stride;
}

/* ---- Wire protocol ----
 *
 * Every message starts with a fixed 32-byte header, optionally followed
 * by `payload_size` bytes of data:
 *
 *   FRAME  producer -> consumer. Buffered: the frame is the payload.
 *          Unbuffered: no payload; `chunk` says where it is in the pool.
 *   REPLY  consumer -> producer. The reply is always the payload; it
 *          also returns the frame's token. `chunk` echoes the frame's.
 *   DROP   consumer -> producer. Returns a frame's token without a
 *          reply (the frame was superseded, or never replied to). */

enum {
  WIRE_FRAME = 1,
  WIRE_REPLY = 2,
  WIRE_DROP = 3,
};

typedef struct {
  uint8_t type;
  uint32_t seq;          /* the frame's seq_num */
  uint32_t chunk;        /* unbuffered: the frame's chunk index */
  uint64_t data_size;    /* size of the frame's data */
  uint64_t payload_size; /* bytes that follow the header on the socket */
} wire_msg_t;

#define WIRE_HDR_SIZE 32

/* Writes one message (header, then `payload_size` bytes of `payload`),
 * blocking until it's all sent. Returns 0 on success, -1 on failure. */
int wire_write(int fd, const wire_msg_t *msg, const uint8_t *payload);

/* Incremental, non-blocking reader for one socket. It remembers a
 * partially received message between calls, so one slow sender can't
 * block the thread that reads many sockets. */
typedef struct {
  uint8_t hdr[WIRE_HDR_SIZE];
  size_t hdr_got;
  wire_msg_t msg;
  uint8_t *payload;
  uint64_t payload_got;
  uint64_t max_payload; /* larger payloads are a protocol violation */
} wire_reader_t;

/* Reads whatever is available on `fd` without blocking. Returns 1 once a
 * whole message has arrived (filling `*msg` and handing over ownership
 * of `*payload`, which is malloc'd, or NULL if empty), 0 if more data is
 * needed, or -1 on EOF, I/O error or protocol violation. */
int wire_read(int fd, wire_reader_t *r, wire_msg_t *msg, uint8_t **payload);

/* Frees any partially received payload. */
void wire_reader_free(wire_reader_t *r);

/* ---- Handshake ----
 *
 * The producer sends a hello (plus, for shm://, its pool's memfd); the
 * consumer answers with its own hello, with `ack` set to HELLO_ACK to
 * accept or 0 to reject. */

enum { MODE_BUFFERED = 0, MODE_UNBUFFERED = 1 };

typedef struct {
  uint8_t mode;         /* producer -> consumer */
  uint8_t ack;          /* consumer -> producer */
  uint64_t id;          /* sender's source_id */
  uint64_t max_send_size;
  uint32_t chunk_count; /* producer, unbuffered: the pool's layout */
  uint64_t chunk_stride;
  char name[LT_NAME_MAX + 1];
  char host[LT_NAME_MAX + 1];
} hello_t;

#define HELLO_ACK 0xAC

int hello_write(int fd, const hello_t *h);
int hello_read(int fd, hello_t *h);

/* ---- Messages ---- */

/* Allocates a message whose identity strings live in the same
 * allocation as the struct. `data` is adopted: lightning_message_free()
 * frees it. */
lightning_message_t *message_new(const hello_t *sender, const char *ip,
                                 uint32_t seq, uint8_t *data,
                                 uint64_t data_size);

#endif /* LIGHTNING_INTERNAL_H */
