/* Helpers shared by the producer and the consumer. Nothing in here
 * knows about producers, consumers or threads. */

#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <netinet/tcp.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/random.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <time.h>
#include <unistd.h>

#include "internal.h"

/* ---- Logging / errors ---- */

static lightning_log_fn g_log_fn = NULL;
static void *g_log_user_data = NULL;

void lightning_set_log_callback(lightning_log_fn fn, void *user_data) {
  g_log_fn = fn;
  g_log_user_data = user_data;
}

void lightning_log(lightning_log_level_t level, const char *fmt, ...) {
  if (g_log_fn == NULL) {
    return;
  }
  char message[256];
  va_list args;
  va_start(args, fmt);
  vsnprintf(message, sizeof(message), fmt, args);
  va_end(args);
  g_log_fn(level, message, g_log_user_data);
}

void set_error(lightning_error_t *error, lightning_error_t value) {
  if (error != NULL) {
    *error = value;
  }
}

const char *lightning_version(void) { return "0.2.0"; }

/* ---- Addresses ---- */

static int parse_unix_path(const char *path, lt_addr_t *out) {
  if (path[0] == '\0' || strlen(path) >= sizeof(out->sa.un.sun_path)) {
    return -1;
  }
  out->sa.un.sun_family = AF_UNIX;
  strcpy(out->sa.un.sun_path, path);
  out->family = AF_UNIX;
  out->len = sizeof(out->sa.un);
  return 0;
}

int parse_address(const char *address, lt_addr_t *out) {
  static const char kTcp[] = "tcp://";
  static const char kUnix[] = "unix://";
  static const char kShm[] = "shm://";

  if (address == NULL) {
    return -1;
  }
  memset(out, 0, sizeof(*out));

  if (strncmp(address, kTcp, strlen(kTcp)) == 0) {
    /* "tcp://ip:port" - split at the last ':'. */
    const char *host_port = address + strlen(kTcp);
    const char *colon = strrchr(host_port, ':');
    if (colon == NULL) {
      return -1;
    }
    char host[INET_ADDRSTRLEN];
    size_t host_len = (size_t)(colon - host_port);
    if (host_len == 0 || host_len >= sizeof(host)) {
      return -1;
    }
    memcpy(host, host_port, host_len);
    host[host_len] = '\0';

    char *end;
    errno = 0;
    long port = strtol(colon + 1, &end, 10);
    if (colon[1] == '\0' || *end != '\0' || errno != 0 || port < 0 ||
        port > 65535) {
      return -1;
    }
    out->sa.in.sin_family = AF_INET;
    out->sa.in.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, host, &out->sa.in.sin_addr) != 1) {
      return -1;
    }
    out->family = AF_INET;
    out->len = sizeof(out->sa.in);
    return 0;
  }
  if (strncmp(address, kUnix, strlen(kUnix)) == 0) {
    return parse_unix_path(address + strlen(kUnix), out);
  }
  if (strncmp(address, kShm, strlen(kShm)) == 0) {
    out->shm = true;
    return parse_unix_path(address + strlen(kShm), out);
  }
  return -1;
}

void peer_ip(int fd, char out[INET_ADDRSTRLEN]) {
  out[0] = '\0';
  struct sockaddr_in sa;
  socklen_t len = sizeof(sa);
  if (getpeername(fd, (struct sockaddr *)&sa, &len) == 0 &&
      sa.sin_family == AF_INET) {
    inet_ntop(AF_INET, &sa.sin_addr, out, INET_ADDRSTRLEN);
  }
}

/* ---- Misc helpers ---- */

uint64_t now_ms(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

uint64_t random_id(void) {
  uint64_t id = 0;
  while (id == 0) {
    if (getrandom(&id, sizeof(id), 0) != (ssize_t)sizeof(id)) {
      /* Practically never happens; fall back to something that still
       * differs between processes and calls. */
      static _Atomic uint64_t counter = 0;
      id = ((uint64_t)getpid() << 32) ^ now_ms() ^ ++counter;
    }
  }
  return id;
}

int send_all(int fd, const void *buf, size_t len) {
  const uint8_t *p = buf;
  while (len > 0) {
    /* MSG_NOSIGNAL: a closed peer must surface as EPIPE, not kill the
     * process with SIGPIPE. */
    ssize_t n = send(fd, p, len, MSG_NOSIGNAL);
    if (n < 0 && errno == EINTR) {
      continue;
    }
    if (n <= 0) {
      return -1;
    }
    p += n;
    len -= (size_t)n;
  }
  return 0;
}

int recv_all(int fd, void *buf, size_t len) {
  uint8_t *p = buf;
  while (len > 0) {
    ssize_t n = recv(fd, p, len, 0);
    if (n < 0 && errno == EINTR) {
      continue;
    }
    if (n <= 0) {
      return -1;
    }
    p += n;
    len -= (size_t)n;
  }
  return 0;
}

int send_fd(int sock, int pass_fd) {
  /* SCM_RIGHTS needs at least one byte of ordinary data to ride on. */
  uint8_t byte = 0;
  struct iovec iov = {.iov_base = &byte, .iov_len = 1};
  union {
    char buf[CMSG_SPACE(sizeof(int))];
    struct cmsghdr align;
  } control;
  memset(&control, 0, sizeof(control));

  struct msghdr msg;
  memset(&msg, 0, sizeof(msg));
  msg.msg_iov = &iov;
  msg.msg_iovlen = 1;
  msg.msg_control = control.buf;
  msg.msg_controllen = sizeof(control.buf);

  struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
  cmsg->cmsg_level = SOL_SOCKET;
  cmsg->cmsg_type = SCM_RIGHTS;
  cmsg->cmsg_len = CMSG_LEN(sizeof(int));
  memcpy(CMSG_DATA(cmsg), &pass_fd, sizeof(int));

  ssize_t n;
  do {
    n = sendmsg(sock, &msg, MSG_NOSIGNAL);
  } while (n < 0 && errno == EINTR);
  return n == 1 ? 0 : -1;
}

int recv_fd(int sock) {
  uint8_t byte;
  struct iovec iov = {.iov_base = &byte, .iov_len = 1};
  union {
    char buf[CMSG_SPACE(sizeof(int))];
    struct cmsghdr align;
  } control;

  struct msghdr msg;
  memset(&msg, 0, sizeof(msg));
  msg.msg_iov = &iov;
  msg.msg_iovlen = 1;
  msg.msg_control = control.buf;
  msg.msg_controllen = sizeof(control.buf);

  ssize_t n;
  do {
    n = recvmsg(sock, &msg, MSG_CMSG_CLOEXEC);
  } while (n < 0 && errno == EINTR);
  if (n != 1) {
    return -1;
  }
  struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
  if (cmsg == NULL || cmsg->cmsg_level != SOL_SOCKET ||
      cmsg->cmsg_type != SCM_RIGHTS) {
    return -1;
  }
  int fd;
  memcpy(&fd, CMSG_DATA(cmsg), sizeof(int));
  return fd;
}

void set_io_timeout(int fd, unsigned ms) {
  struct timeval tv = {.tv_sec = ms / 1000u,
                       .tv_usec = (suseconds_t)(ms % 1000u) * 1000};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
}

void tcp_tune(int fd) {
  /* Frames are latency sensitive: don't let Nagle hold back small
   * writes. */
  int one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
  static const char *const algos[] = {"bbr2", "bbr"};
  for (size_t i = 0; i < sizeof(algos) / sizeof(algos[0]); i++) {
    if (setsockopt(fd, IPPROTO_TCP, TCP_CONGESTION, algos[i],
                   strlen(algos[i])) == 0) {
      return; /* otherwise the OS default stays */
    }
  }
}

/* ---- Shared memory ---- */

int shm_create(lt_shm_t *shm, uint32_t chunk_count, uint64_t max_data) {
  memset(shm, 0, sizeof(*shm));
  shm->fd = -1;
  /* Round each chunk up to a cache line, so neighbouring chunks never
   * share one. */
  uint64_t stride = (max_data + 63u) & ~(uint64_t)63u;
  if (stride == 0) {
    stride = 64;
  }
  if (chunk_count == 0 || stride > SIZE_MAX / chunk_count) {
    return -1;
  }

  int fd = memfd_create("lightning", MFD_CLOEXEC);
  if (fd < 0) {
    return -1;
  }
  size_t size = (size_t)(stride * chunk_count);
  /* ftruncate only sets the size: no memory is used until a page is
   * written. */
  if (ftruncate(fd, (off_t)size) != 0) {
    close(fd);
    return -1;
  }
  void *base = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  if (base == MAP_FAILED) {
    close(fd);
    return -1;
  }
  shm->fd = fd;
  shm->base = base;
  shm->chunk_count = chunk_count;
  shm->stride = stride;
  shm->size = size;
  return 0;
}

int shm_map(lt_shm_t *shm, int fd, uint32_t chunk_count, uint64_t stride) {
  memset(shm, 0, sizeof(*shm));
  shm->fd = -1;
  if (chunk_count == 0 || stride == 0 || stride > SIZE_MAX / chunk_count) {
    close(fd);
    return -1;
  }
  size_t size = (size_t)(stride * chunk_count);
  /* Never trust the peer's layout: mapping past the end of the memfd
   * would crash us with SIGBUS on first access. */
  off_t actual = lseek(fd, 0, SEEK_END);
  if (actual < 0 || (size_t)actual < size) {
    close(fd);
    return -1;
  }
  void *base = mmap(NULL, size, PROT_READ, MAP_SHARED, fd, 0);
  if (base == MAP_FAILED) {
    close(fd);
    return -1;
  }
  shm->fd = fd;
  shm->base = base;
  shm->chunk_count = chunk_count;
  shm->stride = stride;
  shm->size = size;
  return 0;
}

void shm_release(lt_shm_t *shm) {
  if (shm->base != NULL) {
    munmap(shm->base, shm->size);
  }
  if (shm->fd >= 0) {
    close(shm->fd);
  }
  memset(shm, 0, sizeof(*shm));
  shm->fd = -1;
}

/* ---- Wire protocol ---- */

/* Header layout, big-endian:
 *   [0] type  [1..3] reserved  [4..7] seq  [8..11] chunk
 *   [12..15] reserved  [16..23] data_size  [24..31] payload_size */
static void wire_encode(const wire_msg_t *m, uint8_t out[WIRE_HDR_SIZE]) {
  memset(out, 0, WIRE_HDR_SIZE);
  out[0] = m->type;
  uint32_t seq = htobe32(m->seq);
  uint32_t chunk = htobe32(m->chunk);
  uint64_t data_size = htobe64(m->data_size);
  uint64_t payload_size = htobe64(m->payload_size);
  memcpy(out + 4, &seq, 4);
  memcpy(out + 8, &chunk, 4);
  memcpy(out + 16, &data_size, 8);
  memcpy(out + 24, &payload_size, 8);
}

static void wire_decode(const uint8_t in[WIRE_HDR_SIZE], wire_msg_t *m) {
  uint32_t seq, chunk;
  uint64_t data_size, payload_size;
  memcpy(&seq, in + 4, 4);
  memcpy(&chunk, in + 8, 4);
  memcpy(&data_size, in + 16, 8);
  memcpy(&payload_size, in + 24, 8);
  m->type = in[0];
  m->seq = be32toh(seq);
  m->chunk = be32toh(chunk);
  m->data_size = be64toh(data_size);
  m->payload_size = be64toh(payload_size);
}

int wire_write(int fd, const wire_msg_t *msg, const uint8_t *payload) {
  uint8_t hdr[WIRE_HDR_SIZE];
  wire_encode(msg, hdr);
  struct iovec iov[2] = {
      {.iov_base = hdr, .iov_len = WIRE_HDR_SIZE},
      {.iov_base = (void *)payload, .iov_len = (size_t)msg->payload_size},
  };
  struct msghdr mh;
  memset(&mh, 0, sizeof(mh));
  mh.msg_iov = iov;
  mh.msg_iovlen = msg->payload_size > 0 ? 2 : 1;

  /* Header and payload go out in one sendmsg() in the common case. If
   * the socket only takes part of it, advance past what was sent and
   * keep going. */
  size_t remaining = WIRE_HDR_SIZE + (size_t)msg->payload_size;
  while (remaining > 0) {
    ssize_t n = sendmsg(fd, &mh, MSG_NOSIGNAL);
    if (n < 0 && errno == EINTR) {
      continue;
    }
    if (n <= 0) {
      return -1;
    }
    remaining -= (size_t)n;
    size_t skip = (size_t)n;
    while (mh.msg_iovlen > 0 && skip >= mh.msg_iov[0].iov_len) {
      skip -= mh.msg_iov[0].iov_len;
      mh.msg_iov++;
      mh.msg_iovlen--;
    }
    if (mh.msg_iovlen > 0) {
      mh.msg_iov[0].iov_base = (uint8_t *)mh.msg_iov[0].iov_base + skip;
      mh.msg_iov[0].iov_len -= skip;
    }
  }
  return 0;
}

/* One non-blocking recv(). Returns bytes read (> 0), 0 if nothing is
 * available right now, or -1 on EOF or error. */
static ssize_t recv_some(int fd, void *buf, size_t len) {
  for (;;) {
    ssize_t n = recv(fd, buf, len, MSG_DONTWAIT);
    if (n > 0) {
      return n;
    }
    if (n == 0) {
      return -1; /* peer closed */
    }
    if (errno == EINTR) {
      continue;
    }
    return (errno == EAGAIN || errno == EWOULDBLOCK) ? 0 : -1;
  }
}

int wire_read(int fd, wire_reader_t *r, wire_msg_t *msg, uint8_t **payload) {
  /* Phase 1: the fixed-size header. */
  while (r->hdr_got < WIRE_HDR_SIZE) {
    ssize_t n = recv_some(fd, r->hdr + r->hdr_got, WIRE_HDR_SIZE - r->hdr_got);
    if (n <= 0) {
      return (int)n;
    }
    r->hdr_got += (size_t)n;
    if (r->hdr_got < WIRE_HDR_SIZE) {
      continue;
    }
    /* Header complete: learn the payload size and allocate for it. */
    wire_decode(r->hdr, &r->msg);
    if (r->msg.payload_size > r->max_payload) {
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: peer sent a %llu-byte payload, over its "
                    "declared max of %llu",
                    (unsigned long long)r->msg.payload_size,
                    (unsigned long long)r->max_payload);
      return -1;
    }
    r->payload_got = 0;
    r->payload = NULL;
    if (r->msg.payload_size > 0) {
      r->payload = malloc((size_t)r->msg.payload_size);
      if (r->payload == NULL) {
        return -1;
      }
    }
  }

  /* Phase 2: the payload, if any. */
  while (r->payload_got < r->msg.payload_size) {
    ssize_t n = recv_some(fd, r->payload + r->payload_got,
                          (size_t)(r->msg.payload_size - r->payload_got));
    if (n <= 0) {
      return (int)n;
    }
    r->payload_got += (uint64_t)n;
  }

  /* Done: hand the message over and reset for the next one. */
  *msg = r->msg;
  *payload = r->payload;
  r->payload = NULL;
  r->hdr_got = 0;
  r->payload_got = 0;
  return 1;
}

void wire_reader_free(wire_reader_t *r) {
  free(r->payload);
  r->payload = NULL;
  r->hdr_got = 0;
  r->payload_got = 0;
}

/* ---- Handshake ---- */

#define HELLO_MAGIC 0x4C544E47u /* "LTNG" */
#define HELLO_VERSION 2u

/* Fixed part, big-endian: magic(4) version(2) mode(1) ack(1) id(8)
 * max_send(8) chunk_count(4) stride(8) name_len(2) host_len(2) = 40
 * bytes, followed by the name and host strings (no terminators). */
#define HELLO_FIXED 40

int hello_write(int fd, const hello_t *h) {
  uint8_t buf[HELLO_FIXED + 2 * LT_NAME_MAX];
  size_t name_len = strlen(h->name);
  size_t host_len = strlen(h->host);

  uint32_t magic = htobe32(HELLO_MAGIC);
  uint16_t version = htobe16(HELLO_VERSION);
  uint64_t id = htobe64(h->id);
  uint64_t max_send = htobe64(h->max_send_size);
  uint32_t chunk_count = htobe32(h->chunk_count);
  uint64_t stride = htobe64(h->chunk_stride);
  uint16_t wire_name_len = htobe16((uint16_t)name_len);
  uint16_t wire_host_len = htobe16((uint16_t)host_len);

  memcpy(buf + 0, &magic, 4);
  memcpy(buf + 4, &version, 2);
  buf[6] = h->mode;
  buf[7] = h->ack;
  memcpy(buf + 8, &id, 8);
  memcpy(buf + 16, &max_send, 8);
  memcpy(buf + 24, &chunk_count, 4);
  memcpy(buf + 28, &stride, 8);
  memcpy(buf + 36, &wire_name_len, 2);
  memcpy(buf + 38, &wire_host_len, 2);
  memcpy(buf + HELLO_FIXED, h->name, name_len);
  memcpy(buf + HELLO_FIXED + name_len, h->host, host_len);
  return send_all(fd, buf, HELLO_FIXED + name_len + host_len);
}

int hello_read(int fd, hello_t *h) {
  uint8_t buf[HELLO_FIXED];
  if (recv_all(fd, buf, sizeof(buf)) != 0) {
    return -1;
  }
  uint32_t magic, chunk_count;
  uint16_t version, name_len, host_len;
  uint64_t id, max_send, stride;
  memcpy(&magic, buf + 0, 4);
  memcpy(&version, buf + 4, 2);
  memcpy(&id, buf + 8, 8);
  memcpy(&max_send, buf + 16, 8);
  memcpy(&chunk_count, buf + 24, 4);
  memcpy(&stride, buf + 28, 8);
  memcpy(&name_len, buf + 36, 2);
  memcpy(&host_len, buf + 38, 2);

  if (be32toh(magic) != HELLO_MAGIC || be16toh(version) != HELLO_VERSION) {
    lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: handshake from an incompatible peer");
    return -1;
  }
  name_len = be16toh(name_len);
  host_len = be16toh(host_len);
  if (name_len > LT_NAME_MAX || host_len > LT_NAME_MAX) {
    return -1;
  }
  memset(h, 0, sizeof(*h));
  h->mode = buf[6];
  h->ack = buf[7];
  h->id = be64toh(id);
  h->max_send_size = be64toh(max_send);
  h->chunk_count = be32toh(chunk_count);
  h->chunk_stride = be64toh(stride);
  if (recv_all(fd, h->name, name_len) != 0 ||
      recv_all(fd, h->host, host_len) != 0) {
    return -1;
  }
  h->name[name_len] = '\0';
  h->host[host_len] = '\0';
  return 0;
}

/* ---- Messages ---- */

lightning_message_t *message_new(const hello_t *sender, const char *ip,
                                 uint32_t seq, uint8_t *data,
                                 uint64_t data_size) {
  /* One allocation: the struct, then the three identity strings. */
  size_t name_len = strlen(sender->name) + 1;
  size_t ip_len = strlen(ip) + 1;
  size_t host_len = strlen(sender->host) + 1;
  lightning_message_t *msg =
      malloc(sizeof(*msg) + name_len + ip_len + host_len);
  if (msg == NULL) {
    return NULL;
  }
  char *strings = (char *)(msg + 1);
  memcpy(strings, sender->name, name_len);
  memcpy(strings + name_len, ip, ip_len);
  memcpy(strings + name_len + ip_len, sender->host, host_len);
  msg->source_name = strings;
  msg->source_id = sender->id;
  msg->source_ip = strings + name_len;
  msg->source_host = strings + name_len + ip_len;
  msg->seq_num = seq;
  msg->data = data;
  msg->data_size = data_size;
  return msg;
}

void lightning_message_free(lightning_message_t *msg) {
  if (msg == NULL) {
    return;
  }
  free(msg->data);
  free(msg);
}
