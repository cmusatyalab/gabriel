#include "unix.h"

#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <unistd.h>

#include "connection.h"
#include "internal.h"
#include "shmem.h"
#include "utils.h"

/* Creates, mmaps (MAP_SHARED), and shares a new arena of `size` bytes
 * over `fd`. Returns the local mapping, or NULL (with `out_fd` left
 * untouched) on failure. */
static void *create_and_share_arena(int fd, size_t size, int *out_fd) {
  int shm_fd = create_shared_memory(size);
  if (shm_fd < 0) {
    return NULL;
  }
  if (send_shared_memory(fd, shm_fd, size) != 0) {
    close(shm_fd);
    return NULL;
  }
  void *addr = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd, 0);
  if (addr == MAP_FAILED) {
    close(shm_fd);
    return NULL;
  }
  *out_fd = shm_fd;
  return addr;
}

/* Receives and mmaps (MAP_SHARED) an arena shared by the peer via
 * create_and_share_arena(). Returns the local mapping, or NULL (with
 * `out_fd`/`out_size` left untouched) on failure. */
static void *receive_and_map_arena(int fd, int *out_fd, size_t *out_size) {
  int shm_fd;
  uint64_t wire_size;
  if (recv_shared_memory(fd, &shm_fd, &wire_size) != 0) {
    return NULL;
  }
  size_t size = (size_t)wire_size;
  void *addr = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, shm_fd, 0);
  if (addr == MAP_FAILED) {
    close(shm_fd);
    return NULL;
  }
  *out_fd = shm_fd;
  *out_size = size;
  return addr;
}

lightning_connection_t *unix_connect_handshake(int fd, int max_tokens,
                                                size_t shm_size,
                                                lightning_error_t *error) {
  uint32_t wire_max_tokens = htonl((uint32_t)max_tokens);
  uint64_t wire_shm_size = htobe64((uint64_t)shm_size);
  uint8_t ack = 0;
  if (send_all(fd, &wire_max_tokens, sizeof(wire_max_tokens)) != 0 ||
      send_all(fd, &wire_shm_size, sizeof(wire_shm_size)) != 0 ||
      recv_all(fd, &ack, sizeof(ack)) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "unix_connect: handshake failed: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_BROKEN_PIPE);
    return NULL;
  }
  if (ack != LIGHTNING_ACK) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_connect: server sent an unexpected handshake ack");
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }

  size_t send_size = shm_size > 0 ? shm_size : LIGHTNING_SHM_SIZE;
  int send_fd;
  void *send_addr = create_and_share_arena(fd, send_size, &send_fd);
  if (send_addr == NULL) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_connect: failed to create/share arena: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }

  int recv_fd;
  size_t recv_size;
  void *recv_addr = receive_and_map_arena(fd, &recv_fd, &recv_size);
  if (recv_addr == NULL) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_connect: failed to receive peer's arena: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_BROKEN_PIPE);
    munmap(send_addr, send_size);
    close(send_fd);
    return NULL;
  }

  struct lightning_connection_t *conn = malloc(sizeof(*conn));
  if (conn == NULL) {
    set_error(error, LIGHTNING_ERR_INTERNAL);
    munmap(recv_addr, recv_size);
    close(recv_fd);
    munmap(send_addr, send_size);
    close(send_fd);
    return NULL;
  }
  memset(conn, 0, sizeof(*conn));
  conn->fd = fd;
  conn->family = AF_UNIX;
  conn->send_arena_fd = send_fd;
  conn->send_arena_addr = send_addr;
  conn->send_arena_size = send_size;
  conn->have_send_lock = true; /* my arena is freshly created, empty */
  conn->recv_arena_fd = recv_fd;
  conn->recv_arena_addr = recv_addr;
  conn->recv_arena_size = recv_size;
  return conn;
}

lightning_connection_t *unix_accept_handshake(int fd,
                                               lightning_error_t *error) {
  uint32_t wire_max_tokens;
  uint64_t wire_shm_size;
  uint8_t ack = LIGHTNING_ACK;
  if (recv_all(fd, &wire_max_tokens, sizeof(wire_max_tokens)) != 0 ||
      recv_all(fd, &wire_shm_size, sizeof(wire_shm_size)) != 0 ||
      send_all(fd, &ack, sizeof(ack)) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "unix_accept: handshake failed: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_BROKEN_PIPE);
    return NULL;
  }
  (void)wire_max_tokens; /* unix connections aren't token-counted */
  uint64_t requested_shm_size = be64toh(wire_shm_size);

  int recv_fd;
  size_t recv_size;
  void *recv_addr = receive_and_map_arena(fd, &recv_fd, &recv_size);
  if (recv_addr == NULL) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_accept: failed to receive client's arena: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_BROKEN_PIPE);
    return NULL;
  }

  size_t send_size =
      requested_shm_size > 0 ? (size_t)requested_shm_size : LIGHTNING_SHM_SIZE;
  int send_fd;
  void *send_addr = create_and_share_arena(fd, send_size, &send_fd);
  if (send_addr == NULL) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_accept: failed to create/share arena: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_INTERNAL);
    munmap(recv_addr, recv_size);
    close(recv_fd);
    return NULL;
  }

  struct lightning_connection_t *conn = malloc(sizeof(*conn));
  if (conn == NULL) {
    set_error(error, LIGHTNING_ERR_INTERNAL);
    munmap(send_addr, send_size);
    close(send_fd);
    munmap(recv_addr, recv_size);
    close(recv_fd);
    return NULL;
  }
  memset(conn, 0, sizeof(*conn));
  conn->fd = fd;
  conn->family = AF_UNIX;
  conn->send_arena_fd = send_fd;
  conn->send_arena_addr = send_addr;
  conn->send_arena_size = send_size;
  conn->have_send_lock = true; /* my arena is freshly created, empty */
  conn->recv_arena_fd = recv_fd;
  conn->recv_arena_addr = recv_addr;
  conn->recv_arena_size = recv_size;
  return conn;
}

int unix_shared_memory(const lightning_connection_t *conn, int *fd,
                        size_t *size) {
  *fd = conn->recv_arena_fd;
  *size = conn->recv_arena_size;
  return 0;
}

/* Tries to read one header off `fd`, non-blocking. Returns 1 and
 * fills the out-params if a header was read, 0 if nothing is
 * available right now, -1 on a real I/O failure/disconnect, or -2 on
 * allocation failure. Once anything has started arriving, the rest of
 * the (tiny, local-socket) header is read with ordinary blocking
 * calls rather than staying non-blocking field-by-field. */
static int try_read_header(int fd, char **out_source_name, bool *out_token,
                            uint64_t *out_meta_size,
                            uint64_t *out_data_size) {
  uint8_t peek;
  ssize_t n = recv(fd, &peek, 1, MSG_DONTWAIT | MSG_PEEK);
  if (n < 0) {
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
      return 0;
    }
    return -1;
  }
  if (n == 0) {
    return -1; /* peer closed */
  }

  uint32_t wire_source_name_len;
  if (recv_all(fd, &wire_source_name_len, sizeof(wire_source_name_len)) !=
      0) {
    return -1;
  }
  uint32_t source_name_len = ntohl(wire_source_name_len);
  char *source_name = malloc((size_t)source_name_len + 1);
  if (source_name == NULL) {
    return -2;
  }
  if (recv_all(fd, source_name, source_name_len) != 0) {
    free(source_name);
    return -1;
  }
  source_name[source_name_len] = '\0';

  uint8_t wire_token;
  uint64_t wire_meta_size;
  uint64_t wire_data_size;
  if (recv_all(fd, &wire_token, sizeof(wire_token)) != 0 ||
      recv_all(fd, &wire_meta_size, sizeof(wire_meta_size)) != 0 ||
      recv_all(fd, &wire_data_size, sizeof(wire_data_size)) != 0) {
    free(source_name);
    return -1;
  }

  *out_source_name = source_name;
  *out_token = wire_token != 0;
  *out_meta_size = be64toh(wire_meta_size);
  *out_data_size = be64toh(wire_data_size);
  return 1;
}

/* Drains any bare (payload-free) acks waiting on the wire, applying
 * each one's lock grant immediately, and stashes at most one
 * payload-bearing header into conn->pending_* for lightning_recv() to
 * pick up. Idempotent - safe to call from both unix_send() and
 * unix_recv(). */
static lightning_error_t pump_incoming(struct lightning_connection_t *conn) {
  if (conn->has_pending_header) {
    return LIGHTNING_OK;
  }

  for (;;) {
    char *source_name;
    bool token;
    uint64_t meta_size;
    uint64_t data_size;
    int rc = try_read_header(conn->fd, &source_name, &token, &meta_size,
                              &data_size);
    if (rc == 0) {
      return LIGHTNING_OK; /* nothing available right now */
    }
    if (rc == -2) {
      lightning_log(LIGHTNING_LOG_ERROR,
                     "unix: failed to allocate space for an incoming "
                     "message header");
      return LIGHTNING_ERR_INTERNAL;
    }
    if (rc < 0) {
      lightning_log(LIGHTNING_LOG_ERROR,
                     "unix: failed to read an incoming message header: %s",
                     strerror(errno));
      return LIGHTNING_ERR_BROKEN_PIPE;
    }

    if (token) {
      conn->have_send_lock = true;
    }
    if (meta_size == 0 && data_size == 0) {
      /* A bare ack - already applied above, nothing further to do.
       * Keep draining in case a real message immediately follows. */
      free(source_name);
      continue;
    }

    conn->has_pending_header = true;
    conn->pending_source_name = source_name;
    conn->pending_token = token;
    conn->pending_meta_size = meta_size;
    conn->pending_data_size = data_size;
    return LIGHTNING_OK;
  }
}

/* Sends a bare (payload-free) ack granting the peer back its lock.
 * Doesn't touch the arena, so it isn't gated by have_send_lock. */
static int send_bare_ack(int fd) {
  uint32_t wire_source_name_len = htonl(0);
  uint8_t wire_token = 1;
  uint64_t wire_zero = 0;
  return send_all(fd, &wire_source_name_len, sizeof(wire_source_name_len)) ==
             0 &&
                 send_all(fd, &wire_token, sizeof(wire_token)) == 0 &&
                 send_all(fd, &wire_zero, sizeof(wire_zero)) == 0 &&
                 send_all(fd, &wire_zero, sizeof(wire_zero)) == 0
             ? 0
             : -1;
}

lightning_error_t unix_send(lightning_connection_t *conn,
                             const lightning_message_t *msg) {
  if (!conn->have_send_lock) {
    lightning_error_t err = pump_incoming(conn);
    if (err != LIGHTNING_OK) {
      return err;
    }
    if (!conn->have_send_lock) {
      return LIGHTNING_ERR_NO_TOKEN; /* peer hasn't returned the lock yet */
    }
  }

  if (msg->meta_size + msg->data_size > conn->send_arena_size) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_send: message (%llu bytes) doesn't fit in the "
                   "%zu-byte shared memory arena",
                   (unsigned long long)(msg->meta_size + msg->data_size),
                   conn->send_arena_size);
    return LIGHTNING_ERR_INTERNAL;
  }

  if (msg->meta_size > 0) {
    memcpy(conn->send_arena_addr, msg->meta, msg->meta_size);
  }
  if (msg->data_size > 0) {
    memcpy((uint8_t *)conn->send_arena_addr + msg->meta_size, msg->data,
           msg->data_size);
  }

  size_t source_name_len =
      msg->source_name != NULL ? strlen(msg->source_name) : 0;
  uint32_t wire_source_name_len = htonl((uint32_t)source_name_len);
  uint8_t wire_token = msg->token ? 1 : 0;
  uint64_t wire_meta_size = htobe64(msg->meta_size);
  uint64_t wire_data_size = htobe64(msg->data_size);

  if (send_all(conn->fd, &wire_source_name_len,
               sizeof(wire_source_name_len)) != 0 ||
      send_all(conn->fd, msg->source_name, source_name_len) != 0 ||
      send_all(conn->fd, &wire_token, sizeof(wire_token)) != 0 ||
      send_all(conn->fd, &wire_meta_size, sizeof(wire_meta_size)) != 0 ||
      send_all(conn->fd, &wire_data_size, sizeof(wire_data_size)) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_send: failed to write message header: %s",
                   strerror(errno));
    return LIGHTNING_ERR_BROKEN_PIPE;
  }

  conn->have_send_lock = false;
  return LIGHTNING_OK;
}

lightning_error_t unix_recv(lightning_connection_t *conn,
                             lightning_message_t *msg) {
  if (!conn->has_pending_header) {
    lightning_error_t err = pump_incoming(conn);
    if (err != LIGHTNING_OK) {
      return err;
    }
    if (!conn->has_pending_header) {
      return LIGHTNING_ERR_WOULD_BLOCK; /* nothing to read yet */
    }
  }

  uint64_t meta_size = conn->pending_meta_size;
  uint64_t data_size = conn->pending_data_size;

  if (meta_size + data_size > conn->recv_arena_size) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_recv: peer claimed a message (%llu bytes) larger "
                   "than the %zu-byte shared memory arena",
                   (unsigned long long)(meta_size + data_size),
                   conn->recv_arena_size);
    free(conn->pending_source_name);
    conn->pending_source_name = NULL;
    conn->has_pending_header = false;
    return LIGHTNING_ERR_INTERNAL;
  }

  int8_t *meta = NULL;
  if (meta_size > 0) {
    meta = malloc(meta_size);
    if (meta == NULL) {
      return LIGHTNING_ERR_INTERNAL; /* header stays pending, retry later */
    }
  }
  int8_t *data = NULL;
  if (data_size > 0) {
    data = malloc(data_size);
    if (data == NULL) {
      free(meta);
      return LIGHTNING_ERR_INTERNAL;
    }
  }

  if (meta_size > 0) {
    memcpy(meta, conn->recv_arena_addr, meta_size);
  }
  if (data_size > 0) {
    memcpy(data, (uint8_t *)conn->recv_arena_addr + meta_size, data_size);
  }

  msg->source_name = conn->pending_source_name;
  msg->token = conn->pending_token;
  msg->meta_size = meta_size;
  msg->meta = meta;
  msg->data_size = data_size;
  msg->data = data;

  conn->has_pending_header = false;
  conn->pending_source_name = NULL;

  /* Auto-ack: hand the peer's arena lock back now, regardless of
   * whether the caller ever calls unix_send() itself. This doesn't
   * touch the arena, so it isn't gated by have_send_lock. If it fails
   * the connection is presumably dead, but the message we already
   * copied out is still good - don't discard it, the failure will
   * surface on the next call instead. */
  if (send_bare_ack(conn->fd) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "unix_recv: failed to send lock-return ack: %s",
                   strerror(errno));
  }

  return LIGHTNING_OK;
}
