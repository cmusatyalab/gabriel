#include <errno.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <unistd.h>

#include "core.h"

#define HANDSHAKE_TIMEOUT_MS 2000u
#define DEFAULT_REPLY_CHUNKS 8u

/* ---- Helpers ---- */

static bool consumer_enter(lightning_consumer_t *k) {
  pthread_mutex_lock(&k->mu);
  bool ok = !atomic_load(&k->closed);
  if (ok) {
    k->active++;
  }
  pthread_mutex_unlock(&k->mu);
  return ok;
}

static void consumer_leave(lightning_consumer_t *k) {
  pthread_mutex_lock(&k->mu);
  if (--k->active == 0) {
    pthread_cond_broadcast(&k->cond);
  }
  pthread_mutex_unlock(&k->mu);
}

static void signal_fd(int fd) {
  uint64_t one = 1;
  ssize_t n = write(fd, &one, sizeof(one));
  (void)n; /* EAGAIN just means a wakeup is already pending */
}

static void cconn_free(lightning_consumer_t *k, cconn_t *c) {
  while (c->head != NULL) {
    frame_node_t *node = c->head;
    c->head = node->next;
    c->ops->drop_frame(c, &node->msg, node->payload);
    free(node);
  }
  c->ops->on_close(k, c);
  wire_reader_free(&c->reader);
  close(c->fd);
  free(c);
}

/* Returns a token to the producer without a reply. */
static void send_token(cconn_t *c, uint32_t seq, lightning_token_t token) {
  wire_msg_t m = {.type = WIRE_TOKEN, .token = (uint8_t)token, .seq = seq};
  if (wire_write(c->fd, &m, NULL) != 0) {
    c->dead = true;
  }
}

/* ---- Accept thread ---- */

/* Runs the consumer side of the handshake on an accepted socket. */
static cconn_t *consumer_handshake(lightning_consumer_t *k, int fd) {
  if (k->addr.family == AF_INET) {
    tcp_tune(fd);
  }
  set_io_timeout(fd, HANDSHAKE_TIMEOUT_MS);

  cconn_t *c = calloc(1, sizeof(*c));
  if (c == NULL) {
    close(fd);
    return NULL;
  }
  c->pool.fd = -1;
  c->fd = fd;

  if (hello_read(fd, &c->peer) != 0) {
    lightning_log(LIGHTNING_LOG_WARN,
                  "lightning: handshake with a new producer failed");
    goto fail;
  }
  bool shm = c->peer.mode == MODE_UNBUFFERED;
  bool ok = c->peer.mode == MODE_BUFFERED ||
            (shm && k->has_reply_area && c->peer.slot < LIGHTNING_MAX_TARGETS);
  if (!ok) {
    lightning_log(LIGHTNING_LOG_WARN,
                  "lightning: rejected producer '%s' (unbuffered producers "
                  "need a Unix socket consumer)",
                  c->peer.name);
    hello_t nack;
    memset(&nack, 0, sizeof(nack));
    hello_write(fd, &nack);
    goto fail;
  }
  if (shm) {
    int pool_fd = recv_fd(fd);
    if (pool_fd < 0 || shm_map(&c->pool, pool_fd, c->peer.chunk_count,
                               c->peer.chunk_stride) != 0) {
      lightning_log(LIGHTNING_LOG_WARN,
                    "lightning: failed to map the chunk pool of '%s'",
                    c->peer.name);
      goto fail;
    }
  }

  hello_t h;
  memset(&h, 0, sizeof(h));
  h.ack = HELLO_ACK;
  h.mode = c->peer.mode;
  h.id = k->id;
  h.max_send_size = k->max_send_size;
  strcpy(h.name, k->name);
  strcpy(h.host, k->host);
  if (shm) {
    h.chunk_count = k->reply_area.chunk_count;
    h.chunk_stride = k->reply_area.stride;
  }
  if (hello_write(fd, &h) != 0 ||
      (shm && send_fd(fd, k->reply_area.fd) != 0)) {
    goto fail;
  }
  set_io_timeout(fd, 0);

  c->ops = shm ? &transport_unbuffered : &transport_buffered;
  if (++k->next_owner_id == 0) {
    k->next_owner_id = 1;
  }
  c->owner_id = k->next_owner_id;
  peer_ip(fd, c->ip);
  c->reader.max_payload = shm ? 0 : c->peer.max_send_size;
  lightning_log(LIGHTNING_LOG_INFO, "lightning: producer '%s' connected (%s)",
                c->peer.name, shm ? "unbuffered" : "buffered");
  return c;

fail:
  close(fd);
  shm_release(&c->pool);
  free(c);
  return NULL;
}

static void *accept_main(void *arg) {
  lightning_consumer_t *k = arg;
  for (;;) {
    struct pollfd fds[2] = {{.fd = k->listen_fd, .events = POLLIN},
                            {.fd = k->stop_fd, .events = POLLIN}};
    if (poll(fds, 2, -1) < 0 && errno != EINTR) {
      lightning_log(LIGHTNING_LOG_ERROR, "lightning: poll() failed: %s",
                    strerror(errno));
      sleep_us(10000);
    }
    if (atomic_load(&k->closed)) {
      break;
    }
    if (!(fds[0].revents & POLLIN)) {
      continue;
    }
    int fd = accept4(k->listen_fd, NULL, NULL, SOCK_CLOEXEC);
    if (fd < 0) {
      if (errno != EINTR && errno != ECONNABORTED && errno != EAGAIN) {
        lightning_log(LIGHTNING_LOG_ERROR, "lightning: accept() failed: %s",
                      strerror(errno));
        sleep_us(10000);
      }
      continue;
    }
    cconn_t *c = consumer_handshake(k, fd);
    if (c == NULL) {
      continue;
    }

    pthread_mutex_lock(&k->mu);
    cconn_t **tail = &k->pending;
    while (*tail != NULL) {
      tail = &(*tail)->next_pending;
    }
    *tail = c;
    pthread_mutex_unlock(&k->mu);
    signal_fd(k->wake_fd);
  }
  return NULL;
}

/* ---- recv/reply thread ---- */

/* Moves newly accepted producers into the recv thread's list. */
static lightning_error_t merge_pending(lightning_consumer_t *k) {
  pthread_mutex_lock(&k->mu);
  cconn_t *list = k->pending;
  k->pending = NULL;
  pthread_mutex_unlock(&k->mu);

  while (list != NULL) {
    cconn_t *c = list;
    list = c->next_pending;
    c->next_pending = NULL;
    if (k->num_conns == k->cap_conns) {
      size_t cap = k->cap_conns != 0 ? k->cap_conns * 2 : 8;
      cconn_t **conns = realloc(k->conns, cap * sizeof(*conns));
      if (conns == NULL) {
        cconn_free(k, c);
        while (list != NULL) {
          c = list;
          list = c->next_pending;
          cconn_free(k, c);
        }
        return LIGHTNING_ERR_INTERNAL;
      }
      k->conns = conns;
      k->cap_conns = cap;
    }
    k->conns[k->num_conns++] = c;
  }
  return LIGHTNING_OK;
}

/* Queues every frame waiting on `c`'s socket. Returns -1 if the
 * connection broke. */
static int pump_frames(cconn_t *c) {
  for (;;) {
    wire_msg_t m;
    uint8_t *payload = NULL;
    int rc = wire_read(c->fd, &c->reader, &m, &payload);
    if (rc <= 0) {
      return rc;
    }
    if (m.type != WIRE_FRAME) {
      free(payload);
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: unexpected message type %u from '%s'",
                    (unsigned)m.type, c->peer.name);
      return -1;
    }
    frame_node_t *node = malloc(sizeof(*node));
    if (node == NULL) {
      c->ops->drop_frame(c, &m, payload);
      send_token(c, m.seq, LIGHTNING_TOKEN_DROP);
      continue;
    }
    node->msg = m;
    node->payload = payload;
    node->next = NULL;
    if (c->tail != NULL) {
      c->tail->next = node;
    } else {
      c->head = node;
    }
    c->tail = node;
    if (!c->have_newest || seq_gt(m.seq, c->newest_seq)) {
      c->have_newest = true;
      c->newest_seq = m.seq;
    }
  }
}

/* Discards queued frames at least stale_seqs behind the newest one,
 * returning a TOKEN_DROP for each. */
static void drop_stale(cconn_t *c) {
  uint32_t stale = c->peer.stale_seqs;
  if (stale == 0 || !c->have_newest) {
    return;
  }
  frame_node_t *keep_head = NULL;
  frame_node_t *keep_tail = NULL;
  frame_node_t *node = c->head;
  while (node != NULL) {
    frame_node_t *next = node->next;
    node->next = NULL;
    if (!seq_gt(node->msg.seq + stale, c->newest_seq)) {
      c->ops->drop_frame(c, &node->msg, node->payload);
      send_token(c, node->msg.seq, LIGHTNING_TOKEN_DROP);
      free(node);
    } else {
      if (keep_tail != NULL) {
        keep_tail->next = node;
      } else {
        keep_head = node;
      }
      keep_tail = node;
    }
    node = next;
  }
  c->head = keep_head;
  c->tail = keep_tail;
}

/* Frees dead producers (discarding their queued frames), and releases
 * the reply area's memory once no unbuffered producer is left. */
static void remove_dead(lightning_consumer_t *k) {
  size_t kept = 0;
  size_t rr = k->rr;
  bool any_shm = false;
  for (size_t i = 0; i < k->num_conns; i++) {
    cconn_t *c = k->conns[i];
    if (c->dead) {
      lightning_log(LIGHTNING_LOG_INFO,
                    "lightning: producer '%s' disconnected", c->peer.name);
      if (k->last_sender == c) {
        k->last_sender = NULL;
      }
      if (i < k->rr) {
        rr--; /* keep pointing at the same next producer */
      }
      cconn_free(k, c);
      continue;
    }
    any_shm |= c->ops->mode == MODE_UNBUFFERED;
    k->conns[kept++] = c;
  }
  k->num_conns = kept;
  k->rr = rr < kept ? rr : 0;
  if (!any_shm && k->reply_area_dirty) {
    shm_punch(&k->reply_area);
    k->reply_area_dirty = false;
  }
}

/* Returns the next readable frame, round-robin across producers, or
 * NULL if none is queued. */
static lightning_message_t *pick_frame(lightning_consumer_t *k,
                                       lightning_error_t *error) {
  size_t n = k->num_conns;
  for (size_t i = 0; i < n; i++) {
    size_t idx = (k->rr + i) % n;
    cconn_t *c = k->conns[idx];
    while (c->head != NULL && !c->dead) {
      frame_node_t *node = c->head;
      c->head = node->next;
      if (c->head == NULL) {
        c->tail = NULL;
      }
      wire_msg_t m = node->msg;
      uint8_t *payload = node->payload;
      free(node);

      uint8_t *data = NULL;
      if (!c->ops->take_frame(c, &m, payload, &data)) {
        /* Unreadable (reclaimed by the producer): hand the token back
         * so the producer isn't starved. */
        send_token(c, m.seq, LIGHTNING_TOKEN_DROP);
        continue;
      }
      lightning_message_t *msg =
          message_new(c->peer.name, c->peer.id, c->ip, c->peer.host, m.seq,
                      LIGHTNING_TOKEN_NONE, data, m.data_size);
      if (msg == NULL) {
        free(data);
        send_token(c, m.seq, LIGHTNING_TOKEN_DROP);
        set_error(error, LIGHTNING_ERR_INTERNAL);
        return NULL;
      }
      k->rr = (idx + 1) % n;
      k->last_sender = c;
      k->had_recv = true;
      return msg;
    }
  }
  return NULL;
}

/* Blocks until a producer socket or the wake eventfd is readable. */
static lightning_error_t wait_readable(lightning_consumer_t *k) {
  size_t n = k->num_conns;
  struct pollfd *fds = malloc((n + 1) * sizeof(*fds));
  if (fds == NULL) {
    return LIGHTNING_ERR_INTERNAL;
  }
  for (size_t i = 0; i < n; i++) {
    fds[i].fd = k->conns[i]->fd;
    fds[i].events = POLLIN;
    fds[i].revents = 0;
  }
  fds[n].fd = k->wake_fd;
  fds[n].events = POLLIN;
  fds[n].revents = 0;
  if (poll(fds, (nfds_t)n + 1, -1) < 0 && errno != EINTR) {
    free(fds);
    return LIGHTNING_ERR_INTERNAL;
  }
  if (fds[n].revents & POLLIN) {
    uint64_t v;
    ssize_t r = read(k->wake_fd, &v, sizeof(v));
    (void)r;
  }
  free(fds);
  return LIGHTNING_OK;
}

/* ---- API ---- */

lightning_consumer_t *lightning_create_consumer(const char *source_name,
                                                const char *host_name,
                                                uint64_t max_send_size,
                                                const char *address,
                                                uint32_t reply_chunk_count,
                                                lightning_error_t *error) {
  set_error(error, LIGHTNING_OK);
  lt_addr_t addr;
  if (source_name == NULL || strlen(source_name) > LT_NAME_MAX ||
      (host_name != NULL && strlen(host_name) > LT_NAME_MAX) ||
      parse_address(address, &addr) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: invalid consumer arguments (address '%s')",
                  address != NULL ? address : "(null)");
    set_error(error, LIGHTNING_ERR_INVALID);
    return NULL;
  }

  lightning_consumer_t *k = calloc(1, sizeof(*k));
  if (k == NULL) {
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }
  k->id = random_id();
  k->max_send_size = max_send_size;
  strcpy(k->name, source_name);
  strcpy(k->host, host_name != NULL ? host_name : "");
  k->addr = addr;
  k->listen_fd = -1;
  k->wake_fd = -1;
  k->stop_fd = -1;
  k->reply_area.fd = -1;
  pthread_mutex_init(&k->mu, NULL);
  pthread_cond_init(&k->cond, NULL);

  if (addr.family == AF_UNIX) {
    uint32_t chunks =
        reply_chunk_count != 0 ? reply_chunk_count : DEFAULT_REPLY_CHUNKS;
    if (shm_create(&k->reply_area, chunks, max_send_size) != 0) {
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: failed to create the reply area: %s",
                    strerror(errno));
      goto fail;
    }
    k->has_reply_area = true;
  }

  k->listen_fd = socket(addr.family, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (k->listen_fd < 0) {
    goto fail;
  }
  if (addr.family == AF_INET) {
    int reuse = 1;
    setsockopt(k->listen_fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  } else {
    /* Clear a stale socket file left behind by a previous run. */
    unlink(addr.sa.un.sun_path);
  }
  if (bind(k->listen_fd, (struct sockaddr *)&addr.sa, addr.len) != 0 ||
      listen(k->listen_fd, SOMAXCONN) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "lightning: failed to bind '%s': %s",
                  address, strerror(errno));
    goto fail;
  }

  k->wake_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
  k->stop_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
  if (k->wake_fd < 0 || k->stop_fd < 0 ||
      pthread_create(&k->accept_thread, NULL, accept_main, k) != 0) {
    goto fail;
  }
  return k;

fail:
  set_error(error, LIGHTNING_ERR_INTERNAL);
  if (k->listen_fd >= 0) {
    close(k->listen_fd);
    if (addr.family == AF_UNIX) {
      unlink(addr.sa.un.sun_path);
    }
  }
  if (k->wake_fd >= 0) {
    close(k->wake_fd);
  }
  if (k->stop_fd >= 0) {
    close(k->stop_fd);
  }
  shm_release(&k->reply_area);
  pthread_mutex_destroy(&k->mu);
  pthread_cond_destroy(&k->cond);
  free(k);
  return NULL;
}

void lightning_destroy_consumer(lightning_consumer_t *k) {
  if (k == NULL) {
    return;
  }
  pthread_mutex_lock(&k->mu);
  atomic_store(&k->closed, true);
  pthread_mutex_unlock(&k->mu);
  signal_fd(k->stop_fd);
  signal_fd(k->wake_fd);

  pthread_mutex_lock(&k->mu);
  while (k->active > 0) {
    pthread_cond_wait(&k->cond, &k->mu);
  }
  pthread_mutex_unlock(&k->mu);
  pthread_join(k->accept_thread, NULL);

  for (size_t i = 0; i < k->num_conns; i++) {
    cconn_free(k, k->conns[i]);
  }
  free(k->conns);
  while (k->pending != NULL) {
    cconn_t *c = k->pending;
    k->pending = c->next_pending;
    cconn_free(k, c);
  }
  close(k->listen_fd);
  if (k->addr.family == AF_UNIX) {
    unlink(k->addr.sa.un.sun_path);
  }
  close(k->wake_fd);
  close(k->stop_fd);
  shm_release(&k->reply_area);
  pthread_mutex_destroy(&k->mu);
  pthread_cond_destroy(&k->cond);
  free(k);
}

lightning_message_t *lightning_recv(lightning_consumer_t *k,
                                    lightning_error_t *error) {
  if (k == NULL) {
    set_error(error, LIGHTNING_ERR_INVALID);
    return NULL;
  }
  if (!consumer_enter(k)) {
    set_error(error, LIGHTNING_ERR_CLOSED);
    return NULL;
  }
  set_error(error, LIGHTNING_OK);

  lightning_message_t *msg = NULL;
  lightning_error_t err = LIGHTNING_OK;
  while (!atomic_load(&k->closed)) {
    err = merge_pending(k);
    if (err != LIGHTNING_OK) {
      break;
    }
    for (size_t i = 0; i < k->num_conns; i++) {
      cconn_t *c = k->conns[i];
      if (!c->dead && pump_frames(c) != 0) {
        c->dead = true;
      }
      if (!c->dead) {
        drop_stale(c);
      }
    }
    remove_dead(k);

    msg = pick_frame(k, &err);
    if (msg != NULL || err != LIGHTNING_OK) {
      break;
    }
    remove_dead(k); /* a frame's DROP token may have hit a dead peer */
    err = wait_readable(k);
    if (err != LIGHTNING_OK) {
      break;
    }
  }
  if (msg == NULL && err == LIGHTNING_OK) {
    err = LIGHTNING_ERR_CLOSED;
  }
  consumer_leave(k);
  set_error(error, err);
  return msg;
}

lightning_error_t lightning_reply(lightning_consumer_t *k, const uint8_t *data,
                                  uint64_t data_size, uint32_t seq_num) {
  if (k == NULL || (data == NULL && data_size > 0)) {
    return LIGHTNING_ERR_INVALID;
  }
  if (!consumer_enter(k)) {
    return LIGHTNING_ERR_CLOSED;
  }
  lightning_error_t rc;
  cconn_t *c = k->last_sender;
  if (!k->had_recv) {
    rc = LIGHTNING_ERR_INVALID;
  } else if (c == NULL || c->dead) {
    rc = LIGHTNING_ERR_BROKEN_PIPE;
  } else if (data_size > k->max_send_size) {
    rc = LIGHTNING_ERR_TOO_LARGE;
  } else {
    rc = c->ops->reply(k, c, data, data_size, seq_num);
    if (rc == LIGHTNING_ERR_BROKEN_PIPE) {
      c->dead = true;
    }
  }
  consumer_leave(k);
  return rc;
}
