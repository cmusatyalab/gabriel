#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
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

const char *lightning_version(void) { return "0.1.0"; }

/* ---- Addresses ---- */

static int parse_unix_path(const char *path, lt_addr_t *out) {
  if (path[0] == '\0' || strlen(path) >= sizeof(out->sa.un.sun_path)) {
    return -1;
  }
  memset(&out->sa.un, 0, sizeof(out->sa.un));
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
      /* Extremely unlikely; fall back to something that still differs
       * between processes and calls. */
      static _Atomic uint64_t counter = 0;
      id = ((uint64_t)getpid() << 32) ^ now_ms() ^ ++counter;
    }
  }
  return id;
}

void sleep_us(unsigned us) {
  struct timespec ts = {.tv_sec = us / 1000000u,
                        .tv_nsec = (long)(us % 1000000u) * 1000};
  while (nanosleep(&ts, &ts) != 0 && errno == EINTR) {
  }
}

int send_all(int fd, const void *buf, size_t len) {
  const uint8_t *p = buf;
  while (len > 0) {
    /* MSG_NOSIGNAL: a closed peer must surface as EPIPE, not SIGPIPE. */
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

/* ---- Shared memory ---- */

uint64_t shm_stride(uint64_t max_data) {
  return LT_CHUNK_HDR + ((max_data + 63u) & ~(uint64_t)63u);
}

int shm_create(lt_shm_t *shm, uint32_t chunk_count, uint64_t max_data) {
  memset(shm, 0, sizeof(*shm));
  shm->fd = -1;
  shm->chunk_count = chunk_count;
  shm->stride = shm_stride(max_data);
  if (chunk_count == 0 || shm->stride > SIZE_MAX / chunk_count) {
    return -1;
  }
  shm->size = (size_t)(shm->stride * chunk_count);

  int fd = memfd_create("lightning", MFD_CLOEXEC);
  if (fd < 0) {
    return -1;
  }
  if (ftruncate(fd, (off_t)shm->size) != 0) {
    close(fd);
    return -1;
  }
  void *base =
      mmap(NULL, shm->size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  if (base == MAP_FAILED) {
    close(fd);
    return -1;
  }
  shm->fd = fd;
  shm->base = base;
  return 0;
}

int shm_map(lt_shm_t *shm, int fd, uint32_t chunk_count, uint64_t stride) {
  memset(shm, 0, sizeof(*shm));
  shm->fd = -1;
  if (stride < LT_CHUNK_HDR || stride % 64 != 0 || chunk_count == 0) {
    close(fd);
    return -1;
  }
  if (stride > SIZE_MAX / chunk_count) {
    close(fd);
    return -1;
  }
  size_t size = (size_t)(stride * chunk_count);
  off_t actual = lseek(fd, 0, SEEK_END);
  if (actual < 0 || (size_t)actual < size) {
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

void shm_punch(lt_shm_t *shm) {
  if (shm->fd >= 0 && shm->size > 0) {
    /* Round up to whole pages: a partial tail page would only be zeroed,
     * not freed. Punching past EOF is fine with KEEP_SIZE. */
    size_t page = (size_t)sysconf(_SC_PAGESIZE);
    size_t len = (shm->size + page - 1) / page * page;
    fallocate(shm->fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, 0,
              (off_t)len);
  }
}

/* ---- Wire protocol ---- */

/* Header layout (big-endian):
 *   [0] type  [1] token  [2..3] reserved  [4..7] seq  [8..11] chunk
 *   [12..15] reserved  [16..23] data_size  [24..31] payload_size */
static void wire_encode(const wire_msg_t *m, uint8_t out[WIRE_HDR_SIZE]) {
  memset(out, 0, WIRE_HDR_SIZE);
  out[0] = m->type;
  out[1] = m->token;
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
  m->token = in[1];
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

  /* One sendmsg for the common case, then finish any short write. */
  size_t total = WIRE_HDR_SIZE + (size_t)msg->payload_size;
  size_t sent = 0;
  while (sent < total) {
    ssize_t n = sendmsg(fd, &mh, MSG_NOSIGNAL);
    if (n < 0 && errno == EINTR) {
      continue;
    }
    if (n <= 0) {
      return -1;
    }
    sent += (size_t)n;
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

int wire_read(int fd, wire_reader_t *r, wire_msg_t *msg, uint8_t **payload) {
  for (;;) {
    if (r->hdr_got < WIRE_HDR_SIZE) {
      ssize_t n = recv(fd, r->hdr + r->hdr_got, WIRE_HDR_SIZE - r->hdr_got,
                       MSG_DONTWAIT);
      if (n < 0) {
        if (errno == EINTR) {
          continue;
        }
        return (errno == EAGAIN || errno == EWOULDBLOCK) ? 0 : -1;
      }
      if (n == 0) {
        return -1; /* peer closed */
      }
      r->hdr_got += (size_t)n;
      if (r->hdr_got < WIRE_HDR_SIZE) {
        continue;
      }
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

    if (r->payload_got < r->msg.payload_size) {
      ssize_t n = recv(fd, r->payload + r->payload_got,
                       (size_t)(r->msg.payload_size - r->payload_got),
                       MSG_DONTWAIT);
      if (n < 0) {
        if (errno == EINTR) {
          continue;
        }
        return (errno == EAGAIN || errno == EWOULDBLOCK) ? 0 : -1;
      }
      if (n == 0) {
        return -1;
      }
      r->payload_got += (uint64_t)n;
      if (r->payload_got < r->msg.payload_size) {
        continue;
      }
    }

    *msg = r->msg;
    *payload = r->payload;
    r->payload = NULL;
    r->hdr_got = 0;
    r->payload_got = 0;
    return 1;
  }
}

void wire_reader_free(wire_reader_t *r) {
  free(r->payload);
  r->payload = NULL;
  r->hdr_got = 0;
  r->payload_got = 0;
}

/* ---- Handshake ---- */

#define HELLO_MAGIC 0x4C544E47u /* "LTNG" */
#define HELLO_VERSION 1u

/* Fixed part: magic(4) version(2) mode(1) slot(1) ack(1) pad(3) id(8)
 * stale(4) max_send(8) chunk_count(4) stride(8) name_len(2)
 * host_len(2) = 48 bytes, followed by name and host. */
#define HELLO_FIXED 48

int hello_write(int fd, const hello_t *h) {
  uint8_t buf[HELLO_FIXED + 2 * LT_NAME_MAX];
  memset(buf, 0, HELLO_FIXED);
  size_t name_len = strlen(h->name);
  size_t host_len = strlen(h->host);

  uint32_t magic = htobe32(HELLO_MAGIC);
  uint16_t version = htobe16(HELLO_VERSION);
  uint64_t id = htobe64(h->id);
  uint32_t stale = htobe32(h->stale_seqs);
  uint64_t max_send = htobe64(h->max_send_size);
  uint32_t chunk_count = htobe32(h->chunk_count);
  uint64_t stride = htobe64(h->chunk_stride);
  uint16_t wire_name_len = htobe16((uint16_t)name_len);
  uint16_t wire_host_len = htobe16((uint16_t)host_len);

  memcpy(buf + 0, &magic, 4);
  memcpy(buf + 4, &version, 2);
  buf[6] = h->mode;
  buf[7] = h->slot;
  buf[8] = h->ack;
  memcpy(buf + 12, &id, 8);
  memcpy(buf + 20, &stale, 4);
  memcpy(buf + 24, &max_send, 8);
  memcpy(buf + 32, &chunk_count, 4);
  memcpy(buf + 36, &stride, 8);
  memcpy(buf + 44, &wire_name_len, 2);
  memcpy(buf + 46, &wire_host_len, 2);
  memcpy(buf + HELLO_FIXED, h->name, name_len);
  memcpy(buf + HELLO_FIXED + name_len, h->host, host_len);
  return send_all(fd, buf, HELLO_FIXED + name_len + host_len);
}

int hello_read(int fd, hello_t *h) {
  uint8_t buf[HELLO_FIXED];
  if (recv_all(fd, buf, sizeof(buf)) != 0) {
    return -1;
  }
  uint32_t magic;
  uint16_t version, name_len, host_len;
  uint64_t id, max_send, stride;
  uint32_t stale, chunk_count;
  memcpy(&magic, buf + 0, 4);
  memcpy(&version, buf + 4, 2);
  memcpy(&id, buf + 12, 8);
  memcpy(&stale, buf + 20, 4);
  memcpy(&max_send, buf + 24, 8);
  memcpy(&chunk_count, buf + 32, 4);
  memcpy(&stride, buf + 36, 8);
  memcpy(&name_len, buf + 44, 2);
  memcpy(&host_len, buf + 46, 2);

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
  h->slot = buf[7];
  h->ack = buf[8];
  h->id = be64toh(id);
  h->stale_seqs = be32toh(stale);
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

lightning_message_t *message_new(const char *name, uint64_t id,
                                 const char *ip, const char *host,
                                 uint32_t seq, lightning_token_t token,
                                 uint8_t *data, uint64_t data_size) {
  size_t name_len = strlen(name) + 1;
  size_t ip_len = strlen(ip) + 1;
  size_t host_len = strlen(host) + 1;
  lightning_message_t *msg =
      malloc(sizeof(*msg) + name_len + ip_len + host_len);
  if (msg == NULL) {
    return NULL;
  }
  char *strings = (char *)(msg + 1);
  memcpy(strings, name, name_len);
  memcpy(strings + name_len, ip, ip_len);
  memcpy(strings + name_len + ip_len, host, host_len);
  msg->source_name = strings;
  msg->source_id = id;
  msg->source_ip = strings + name_len;
  msg->source_host = strings + name_len + ip_len;
  msg->seq_num = seq;
  msg->token = token;
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
