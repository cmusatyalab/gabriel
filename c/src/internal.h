#ifndef LIGHTNING_INTERNAL_H
#define LIGHTNING_INTERNAL_H

#include <netinet/in.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/socket.h>
#include <sys/un.h>

#include "lightning/lightning.h"

/* ---- Logging / errors ---- */

void lightning_log(lightning_log_level_t level, const char *fmt, ...)
    __attribute__((format(printf, 2, 3)));

/* Sets *error to value if error is non-NULL. */
void set_error(lightning_error_t *error, lightning_error_t value);

/* ---- Sequence numbers ---- */

/* Serial number comparison (RFC 1982 style), so seq_nums may wrap. */
static inline bool seq_gt(uint32_t a, uint32_t b) {
  return (int32_t)(a - b) > 0;
}

/* ---- Addresses ---- */

#define LT_NAME_MAX 255

typedef struct {
  int family; /* AF_INET or AF_UNIX */
  bool shm;   /* "shm://" (unbuffered) */
  socklen_t len;
  union {
    struct sockaddr_in in;
    struct sockaddr_un un;
  } sa;
} lt_addr_t;

/* Parses "tcp://ip:port", "unix://path" or "shm://path". Returns 0 on
 * success, -1 if malformed. */
int parse_address(const char *address, lt_addr_t *out);

/* ---- Misc helpers ---- */

uint64_t now_ms(void);
uint64_t random_id(void);
void sleep_us(unsigned us);

/* Blocking send()/recv() of exactly `len` bytes, retrying on EINTR.
 * Return 0 on success, -1 on error/disconnection. */
int send_all(int fd, const void *buf, size_t len);
int recv_all(int fd, void *buf, size_t len);

/* Passes `pass_fd` (plus one byte) over a Unix socket via SCM_RIGHTS,
 * and receives it on the other end. recv_fd returns -1 on failure. */
int send_fd(int sock, int pass_fd);
int recv_fd(int sock);

/* Applies a send/recv timeout to `fd` (0 clears it). */
void set_io_timeout(int fd, unsigned ms);

/* TCP_NODELAY plus the best available congestion control. */
void tcp_tune(int fd);

/* ---- Shared memory ---- */

/* Every chunk (frame or reply) starts with a 64-bit atomic header,
 * padded to a cache line, followed by the data. */
#define LT_CHUNK_HDR 64

typedef struct {
  int fd;
  uint8_t *base;
  uint32_t chunk_count;
  uint64_t stride;
  size_t size;
} lt_shm_t;

uint64_t shm_stride(uint64_t max_data);
/* Creates and maps a memfd of `chunk_count` chunks. */
int shm_create(lt_shm_t *shm, uint32_t chunk_count, uint64_t max_data);
/* Maps a memfd received from the peer (takes ownership of `fd`). */
int shm_map(lt_shm_t *shm, int fd, uint32_t chunk_count, uint64_t stride);
void shm_release(lt_shm_t *shm);
/* Returns the memfd's pages to the kernel; the mapping stays valid and
 * reads back as zeros. */
void shm_punch(lt_shm_t *shm);

static inline _Atomic uint64_t *shm_hdr(const lt_shm_t *shm, uint32_t i) {
  return (_Atomic uint64_t *)(shm->base + (uint64_t)i * shm->stride);
}
static inline uint8_t *shm_data(const lt_shm_t *shm, uint32_t i) {
  return shm->base + (uint64_t)i * shm->stride + LT_CHUNK_HDR;
}

/* Producer chunk pool header: [63..33] reader mask, [32] write lock,
 * [31..0] seq_num. */
#define POOL_SEQ(h) ((uint32_t)((h) & 0xffffffffu))
#define POOL_LOCK (1ull << 32)
#define POOL_BIT(slot) (1ull << (33 + (slot)))
#define POOL_MASK_ALL (~0ull << 33)

/* Consumer reply area header: [63..32] owner (0 = free), [31..0]
 * seq_num. */
#define REPLY_OWNER(h) ((uint32_t)((h) >> 32))
#define REPLY_HDR(owner, seq) (((uint64_t)(owner) << 32) | (uint32_t)(seq))

/* ---- Wire protocol ---- */

enum {
  WIRE_FRAME = 1, /* producer -> consumer */
  WIRE_REPLY = 2, /* consumer -> producer, carries TOKEN_ACCEPT */
  WIRE_TOKEN = 3, /* consumer -> producer, token only (TOKEN_DROP) */
};

typedef struct {
  uint8_t type;
  uint8_t token;
  uint32_t seq;
  uint32_t chunk;        /* unbuffered: chunk index */
  uint64_t data_size;    /* size of the frame/reply data */
  uint64_t payload_size; /* bytes that follow on the socket (buffered) */
} wire_msg_t;

#define WIRE_HDR_SIZE 32

/* Writes one message (header, then `payload_size` bytes of payload),
 * blocking. Returns 0 on success, -1 on failure. */
int wire_write(int fd, const wire_msg_t *msg, const uint8_t *payload);

/* Incremental non-blocking reader for one socket. */
typedef struct {
  uint8_t hdr[WIRE_HDR_SIZE];
  size_t hdr_got;
  wire_msg_t msg;
  uint8_t *payload;
  uint64_t payload_got;
  uint64_t max_payload; /* larger payloads are a protocol error */
} wire_reader_t;

/* Reads whatever is available on `fd`. Returns 1 with `*msg` filled and
 * ownership of `*payload` (malloc'd, NULL if empty) passed to the
 * caller once a whole message has arrived, 0 if more data is needed,
 * or -1 on EOF, I/O error or protocol violation. */
int wire_read(int fd, wire_reader_t *r, wire_msg_t *msg, uint8_t **payload);
void wire_reader_free(wire_reader_t *r);

/* ---- Handshake ---- */

enum { MODE_BUFFERED = 0, MODE_UNBUFFERED = 1 };

typedef struct {
  uint8_t mode;
  uint8_t slot; /* producer's bit slot for this consumer */
  uint8_t ack;  /* consumer -> producer only */
  uint64_t id;
  uint32_t stale_seqs;
  uint64_t max_send_size;
  uint32_t chunk_count; /* unbuffered: shared memory layout */
  uint64_t chunk_stride;
  char name[LT_NAME_MAX + 1];
  char host[LT_NAME_MAX + 1];
} hello_t;

#define HELLO_ACK 0xAC

int hello_write(int fd, const hello_t *h);
int hello_read(int fd, hello_t *h);

/* ---- Messages ---- */

/* Allocates a message whose identity strings live in the same
 * allocation as the struct. `data` is adopted (freed by
 * lightning_message_free). */
lightning_message_t *message_new(const char *name, uint64_t id,
                                 const char *ip, const char *host,
                                 uint32_t seq, lightning_token_t token,
                                 uint8_t *data, uint64_t data_size);

/* Fills `out` with the peer's IPv4 address, or "" for a Unix socket. */
void peer_ip(int fd, char out[INET_ADDRSTRLEN]);

#endif /* LIGHTNING_INTERNAL_H */
