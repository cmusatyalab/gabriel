#include <errno.h>
#include <fcntl.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <time.h>
#include <unistd.h>

#include "core.h"

#define CONNECT_TIMEOUT_MS 1000u
#define HANDSHAKE_TIMEOUT_MS 2000u
#define BACKOFF_MIN_MS 50u
#define BACKOFF_MAX_MS 1000u

/* ---- Helpers ---- */

static bool producer_enter(lightning_producer_t *p) {
  pthread_mutex_lock(&p->mu);
  bool ok = !atomic_load(&p->closed);
  if (ok) {
    p->active++;
  }
  pthread_mutex_unlock(&p->mu);
  return ok;
}

static void producer_leave(lightning_producer_t *p) {
  pthread_mutex_lock(&p->mu);
  if (--p->active == 0) {
    pthread_cond_broadcast(&p->cond);
  }
  pthread_mutex_unlock(&p->mu);
}

static void wake_reader(lightning_producer_t *p) {
  uint64_t one = 1;
  ssize_t n = write(p->wake_fd, &one, sizeof(one));
  (void)n; /* EAGAIN just means a wakeup is already pending */
}

void tcp_tune(int fd) {
  int one = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
  static const char *const algos[] = {"bbr2", "bbr"};
  for (size_t i = 0; i < sizeof(algos) / sizeof(algos[0]); i++) {
    if (setsockopt(fd, IPPROTO_TCP, TCP_CONGESTION, algos[i],
                   strlen(algos[i])) == 0) {
      return;
    }
  }
}

void pconn_unref(pconn_t *c) {
  if (atomic_fetch_sub(&c->refs, 1) != 1) {
    return;
  }
  close(c->fd);
  wire_reader_free(&c->reader);
  shm_release(&c->reply_area);
  pthread_mutex_destroy(&c->mu);
  pthread_mutex_destroy(&c->wmu);
  free(c);
}

static void free_target(target_t *t) {
  free(t->address);
  free(t);
}

static target_t *find_target(lightning_producer_t *p, const char *address) {
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    if (p->targets[i] != NULL && strcmp(p->targets[i]->address, address) == 0) {
      return p->targets[i];
    }
  }
  return NULL;
}

/* ---- Connecting ---- */

static int connect_with_timeout(int fd, const lt_addr_t *addr, unsigned ms) {
  int flags = fcntl(fd, F_GETFL);
  if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) != 0) {
    return -1;
  }
  if (connect(fd, (const struct sockaddr *)&addr->sa, addr->len) != 0) {
    if (errno != EINPROGRESS) {
      return -1;
    }
    struct pollfd pfd = {.fd = fd, .events = POLLOUT};
    int rc;
    do {
      rc = poll(&pfd, 1, (int)ms);
    } while (rc < 0 && errno == EINTR);
    if (rc <= 0) {
      errno = rc == 0 ? ETIMEDOUT : errno;
      return -1;
    }
    int err = 0;
    socklen_t len = sizeof(err);
    if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) != 0 || err != 0) {
      errno = err;
      return -1;
    }
  }
  return fcntl(fd, F_SETFL, flags);
}

/* Connects to `addr` and runs the producer side of the handshake.
 * Doesn't touch any mutable producer state, so it runs without p->mu. */
static pconn_t *producer_connect(lightning_producer_t *p, const char *address,
                                 const lt_addr_t *addr, uint8_t slot) {
  int fd = socket(addr->family, SOCK_STREAM | SOCK_CLOEXEC, 0);
  if (fd < 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "lightning: socket() failed: %s",
                  strerror(errno));
    return NULL;
  }
  if (connect_with_timeout(fd, addr, CONNECT_TIMEOUT_MS) != 0) {
    lightning_log(LIGHTNING_LOG_DEBUG, "lightning: connect to '%s' failed: %s",
                  address, strerror(errno));
    close(fd);
    return NULL;
  }
  if (addr->family == AF_INET) {
    tcp_tune(fd);
  }
  set_io_timeout(fd, HANDSHAKE_TIMEOUT_MS);

  const transport_t *ops =
      addr->shm ? &transport_unbuffered : &transport_buffered;
  pconn_t *c = calloc(1, sizeof(*c));
  if (c == NULL) {
    close(fd);
    return NULL;
  }
  c->reply_area.fd = -1;

  hello_t h;
  memset(&h, 0, sizeof(h));
  h.mode = ops->mode;
  h.slot = slot;
  h.id = p->id;
  h.stale_seqs = p->stale_seqs;
  h.max_send_size = p->max_send_size;
  if (addr->shm) {
    h.chunk_count = p->pool.chunk_count;
    h.chunk_stride = p->pool.stride;
  }
  strcpy(h.name, p->name);
  strcpy(h.host, p->host);

  if (hello_write(fd, &h) != 0 ||
      (addr->shm && send_fd(fd, p->pool.fd) != 0) ||
      hello_read(fd, &c->peer) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: handshake with '%s' failed: %s", address,
                  strerror(errno));
    goto fail;
  }
  if (c->peer.ack != HELLO_ACK) {
    lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: '%s' rejected the connection (an unbuffered "
                  "producer needs a Unix socket consumer)",
                  address);
    goto fail;
  }
  if (addr->shm) {
    int area_fd = recv_fd(fd);
    if (area_fd < 0 ||
        shm_map(&c->reply_area, area_fd, c->peer.chunk_count,
                c->peer.chunk_stride) != 0) {
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: failed to map the reply area of '%s'",
                    address);
      goto fail;
    }
  }
  set_io_timeout(fd, 0);

  atomic_init(&c->refs, 1);
  c->ops = ops;
  c->fd = fd;
  c->slot = slot;
  peer_ip(fd, c->ip);
  pthread_mutex_init(&c->mu, NULL);
  pthread_mutex_init(&c->wmu, NULL);
  c->tokens = p->max_tokens;
  c->reader.max_payload = addr->shm ? 0 : c->peer.max_send_size;
  lightning_log(LIGHTNING_LOG_INFO, "lightning: connected to '%s' (%s)",
                address, addr->shm ? "unbuffered" : "buffered");
  return c;

fail:
  close(fd);
  shm_release(&c->reply_area);
  free(c);
  return NULL;
}

/* Marks `t`'s connection dead and detaches it. p->mu held. Returns the
 * connection so the caller can drop the target's reference outside the
 * lock. */
static pconn_t *detach_conn(lightning_producer_t *p, target_t *t) {
  pconn_t *c = t->conn;
  if (c == NULL) {
    return NULL;
  }
  t->conn = NULL;
  pthread_mutex_lock(&c->mu);
  c->dead = true;
  pthread_mutex_unlock(&c->mu);
  /* After `dead` is set nobody hands this consumer frames any more, so
   * its bit can be cleared for good. */
  c->ops->on_lost(p, c);
  shutdown(c->fd, SHUT_RDWR);
  return c;
}

/* Completes a connect attempt made outside p->mu. p->mu held. */
static void finish_connect(lightning_producer_t *p, target_t *t, pconn_t *c) {
  t->connecting = false;
  if (t->removed || atomic_load(&p->closed)) {
    if (t->removed) {
      free_target(t);
    }
    if (c != NULL) {
      pconn_unref(c);
    }
    return;
  }
  if (c == NULL) {
    t->next_attempt_ms = now_ms() + t->backoff_ms;
    t->backoff_ms = t->backoff_ms * 2 < BACKOFF_MAX_MS ? t->backoff_ms * 2
                                                       : BACKOFF_MAX_MS;
    pthread_cond_broadcast(&p->cond);
    return;
  }
  if (c->ops->mode == MODE_UNBUFFERED) {
    /* Never hand a new consumer a frame from before it connected. */
    c->have_last_seq = atomic_load(&p->have_published);
    c->last_seq = atomic_load(&p->latest_seq);
  }
  t->conn = c;
  t->backoff_ms = BACKOFF_MIN_MS;
  wake_reader(p);
}

/* Handles a broken connection found by any thread. */
static void conn_lost(lightning_producer_t *p, pconn_t *c) {
  pconn_t *detached = NULL;
  pthread_mutex_lock(&p->mu);
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    target_t *t = p->targets[i];
    if (t != NULL && t->conn == c) {
      detached = detach_conn(p, t);
      lightning_log(LIGHTNING_LOG_WARN,
                    "lightning: lost connection to '%s', reconnecting",
                    t->address);
      t->next_attempt_ms = now_ms() + t->backoff_ms;
      pthread_cond_broadcast(&p->cond);
      break;
    }
  }
  pthread_mutex_unlock(&p->mu);
  if (detached != NULL) {
    pconn_unref(detached);
  }
}

static void *connector_main(void *arg) {
  lightning_producer_t *p = arg;
  pthread_mutex_lock(&p->mu);
  while (!atomic_load(&p->closed)) {
    uint64_t now = now_ms();
    uint64_t next = UINT64_MAX;
    target_t *pick = NULL;
    for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
      target_t *t = p->targets[i];
      if (t == NULL || t->conn != NULL || t->connecting) {
        continue;
      }
      if (t->next_attempt_ms <= now) {
        pick = t;
        break;
      }
      if (t->next_attempt_ms < next) {
        next = t->next_attempt_ms;
      }
    }

    if (pick != NULL) {
      pick->connecting = true;
      lt_addr_t addr = pick->addr;
      uint8_t slot = pick->slot;
      char *address = strdup(pick->address);
      pthread_mutex_unlock(&p->mu);
      pconn_t *c = address != NULL ? producer_connect(p, address, &addr, slot)
                                   : NULL;
      free(address);
      pthread_mutex_lock(&p->mu);
      finish_connect(p, pick, c);
      continue;
    }

    if (next == UINT64_MAX) {
      pthread_cond_wait(&p->cond, &p->mu);
    } else {
      struct timespec ts;
      clock_gettime(CLOCK_MONOTONIC, &ts);
      uint64_t wait = next - now;
      ts.tv_sec += (time_t)(wait / 1000u);
      ts.tv_nsec += (long)(wait % 1000u) * 1000000L;
      if (ts.tv_nsec >= 1000000000L) {
        ts.tv_sec++;
        ts.tv_nsec -= 1000000000L;
      }
      pthread_cond_timedwait(&p->cond, &p->mu, &ts);
    }
  }
  pthread_mutex_unlock(&p->mu);
  return NULL;
}

/* ---- Reader thread ---- */

static void push_reply(lightning_producer_t *p, lightning_message_t *msg) {
  reply_node_t *node = malloc(sizeof(*node));
  if (node == NULL) {
    lightning_message_free(msg);
    return;
  }
  node->msg = msg;
  node->next = NULL;
  pthread_mutex_lock(&p->rq_mu);
  if (p->rq_tail != NULL) {
    p->rq_tail->next = node;
  } else {
    p->rq_head = node;
  }
  p->rq_tail = node;
  pthread_cond_signal(&p->rq_cond);
  pthread_mutex_unlock(&p->rq_mu);
}

static int handle_token(lightning_producer_t *p, pconn_t *c, uint32_t seq) {
  pthread_mutex_lock(&c->mu);
  /* Count each frame's token once, even if it comes back twice. */
  if (!c->have_token_seq || seq_gt(seq, c->token_seq)) {
    c->have_token_seq = true;
    c->token_seq = seq;
    if (c->tokens < p->max_tokens) {
      c->tokens++;
    }
  }
  int rc = c->dead ? 0 : c->ops->on_token(p, c);
  pthread_mutex_unlock(&c->mu);
  return rc;
}

/* Processes everything waiting on `c`'s socket. Returns -1 if the
 * connection broke. */
static int pump(lightning_producer_t *p, pconn_t *c) {
  for (;;) {
    wire_msg_t m;
    uint8_t *payload = NULL;
    int rc = wire_read(c->fd, &c->reader, &m, &payload);
    if (rc <= 0) {
      return rc;
    }
    if (m.type != WIRE_REPLY && m.type != WIRE_TOKEN) {
      free(payload);
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: unexpected message type %u from '%s'",
                    (unsigned)m.type, c->peer.name);
      return -1;
    }

    lightning_message_t *msg = NULL;
    if (m.type == WIRE_REPLY) {
      uint8_t *data = NULL;
      if (c->ops->take_reply(c, &m, payload, &data)) {
        msg = message_new(c->peer.name, c->peer.id, c->ip, c->peer.host, m.seq,
                          LIGHTNING_TOKEN_ACCEPT, data, m.data_size);
        if (msg == NULL) {
          free(data);
        }
      } else {
        lightning_log(LIGHTNING_LOG_WARN,
                      "lightning: discarded an unreadable reply from '%s'",
                      c->peer.name);
      }
    } else {
      free(payload);
    }

    /* Count the token before queueing the reply, so a caller that got
     * the reply from lightning_recv_reply() can rely on the token being
     * back. */
    rc = handle_token(p, c, m.seq);
    if (msg != NULL) {
      push_reply(p, msg);
    }
    if (rc != 0) {
      return -1;
    }
  }
}

static void *reader_main(void *arg) {
  lightning_producer_t *p = arg;
  pconn_t *conns[LIGHTNING_MAX_TARGETS];
  struct pollfd fds[LIGHTNING_MAX_TARGETS + 1];

  for (;;) {
    int n = 0;
    pthread_mutex_lock(&p->mu);
    if (atomic_load(&p->closed)) {
      pthread_mutex_unlock(&p->mu);
      break;
    }
    for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
      target_t *t = p->targets[i];
      if (t != NULL && t->conn != NULL) {
        atomic_fetch_add(&t->conn->refs, 1);
        conns[n] = t->conn;
        fds[n].fd = t->conn->fd;
        fds[n].events = POLLIN;
        fds[n].revents = 0;
        n++;
      }
    }
    pthread_mutex_unlock(&p->mu);
    fds[n].fd = p->wake_fd;
    fds[n].events = POLLIN;
    fds[n].revents = 0;

    if (poll(fds, (nfds_t)n + 1, -1) < 0 && errno != EINTR) {
      lightning_log(LIGHTNING_LOG_ERROR, "lightning: poll() failed: %s",
                    strerror(errno));
    }
    if (fds[n].revents & POLLIN) {
      uint64_t v;
      ssize_t r = read(p->wake_fd, &v, sizeof(v));
      (void)r;
    }
    for (int i = 0; i < n; i++) {
      if (fds[i].revents != 0 && pump(p, conns[i]) != 0) {
        conn_lost(p, conns[i]);
      }
    }
    for (int i = 0; i < n; i++) {
      pconn_unref(conns[i]);
    }
  }
  return NULL;
}

/* ---- API ---- */

lightning_producer_t *lightning_create_producer(
    uint32_t max_tokens, uint64_t max_send_size, uint32_t stale_seqs,
    const char *source_name, const char *host_name, const char **targets,
    lightning_error_t *error) {
  set_error(error, LIGHTNING_OK);
  if (max_tokens == 0 || source_name == NULL ||
      strlen(source_name) > LT_NAME_MAX ||
      (host_name != NULL && strlen(host_name) > LT_NAME_MAX)) {
    set_error(error, LIGHTNING_ERR_INVALID);
    return NULL;
  }

  lightning_producer_t *p = calloc(1, sizeof(*p));
  if (p == NULL) {
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }
  p->max_tokens = max_tokens;
  p->max_send_size = max_send_size;
  p->stale_seqs = stale_seqs;
  p->id = random_id();
  strcpy(p->name, source_name);
  strcpy(p->host, host_name != NULL ? host_name : "");
  p->wake_fd = -1;

  pthread_condattr_t attr;
  pthread_condattr_init(&attr);
  pthread_condattr_setclock(&attr, CLOCK_MONOTONIC);
  pthread_mutex_init(&p->mu, NULL);
  pthread_cond_init(&p->cond, &attr);
  pthread_condattr_destroy(&attr);
  pthread_mutex_init(&p->send_mu, NULL);
  pthread_mutex_init(&p->rq_mu, NULL);
  pthread_cond_init(&p->rq_cond, NULL);

  /* The pool only reserves address space until a frame is written for
   * an unbuffered consumer. */
  if (shm_create(&p->pool, max_tokens, max_send_size) != 0 ||
      (p->valid = calloc(max_tokens, sizeof(*p->valid))) == NULL ||
      (p->sizes = calloc(max_tokens, sizeof(*p->sizes))) == NULL ||
      (p->wake_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK)) < 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: failed to allocate producer resources: %s",
                  strerror(errno));
    goto fail_early;
  }

  if (pthread_create(&p->reader_thread, NULL, reader_main, p) != 0) {
    goto fail_early;
  }
  if (pthread_create(&p->connector_thread, NULL, connector_main, p) != 0) {
    atomic_store(&p->closed, true);
    wake_reader(p);
    pthread_join(p->reader_thread, NULL);
    goto fail_early;
  }

  for (size_t i = 0; targets != NULL && targets[i] != NULL; i++) {
    lightning_error_t rc = lightning_add_target(p, targets[i]);
    if (rc != LIGHTNING_OK) {
      set_error(error, rc);
      lightning_destroy_producer(p);
      return NULL;
    }
  }
  return p;

fail_early:
  set_error(error, LIGHTNING_ERR_INTERNAL);
  if (p->wake_fd >= 0) {
    close(p->wake_fd);
  }
  free(p->valid);
  free(p->sizes);
  shm_release(&p->pool);
  pthread_mutex_destroy(&p->mu);
  pthread_cond_destroy(&p->cond);
  pthread_mutex_destroy(&p->send_mu);
  pthread_mutex_destroy(&p->rq_mu);
  pthread_cond_destroy(&p->rq_cond);
  free(p);
  return NULL;
}

void lightning_destroy_producer(lightning_producer_t *p) {
  if (p == NULL) {
    return;
  }
  pthread_mutex_lock(&p->mu);
  atomic_store(&p->closed, true);
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    if (p->targets[i] != NULL && p->targets[i]->conn != NULL) {
      /* Unblocks a send stuck writing to a stalled consumer. */
      shutdown(p->targets[i]->conn->fd, SHUT_RDWR);
    }
  }
  pthread_cond_broadcast(&p->cond);
  pthread_mutex_unlock(&p->mu);
  wake_reader(p);
  pthread_mutex_lock(&p->rq_mu);
  pthread_cond_broadcast(&p->rq_cond);
  pthread_mutex_unlock(&p->rq_mu);

  pthread_mutex_lock(&p->mu);
  while (p->active > 0) {
    pthread_cond_wait(&p->cond, &p->mu);
  }
  pthread_mutex_unlock(&p->mu);
  pthread_join(p->reader_thread, NULL);
  pthread_join(p->connector_thread, NULL);

  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    target_t *t = p->targets[i];
    if (t != NULL) {
      if (t->conn != NULL) {
        pconn_unref(t->conn);
      }
      free_target(t);
    }
  }
  while (p->rq_head != NULL) {
    reply_node_t *node = p->rq_head;
    p->rq_head = node->next;
    lightning_message_free(node->msg);
    free(node);
  }
  close(p->wake_fd);
  free(p->valid);
  free(p->sizes);
  shm_release(&p->pool);
  pthread_mutex_destroy(&p->mu);
  pthread_cond_destroy(&p->cond);
  pthread_mutex_destroy(&p->send_mu);
  pthread_mutex_destroy(&p->rq_mu);
  pthread_cond_destroy(&p->rq_cond);
  free(p);
}

lightning_error_t lightning_add_target(lightning_producer_t *p,
                                       const char *address) {
  lt_addr_t addr;
  if (p == NULL || parse_address(address, &addr) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "lightning: invalid address '%s'",
                  address != NULL ? address : "(null)");
    return LIGHTNING_ERR_INVALID;
  }
  if (!producer_enter(p)) {
    return LIGHTNING_ERR_CLOSED;
  }

  pthread_mutex_lock(&p->mu);
  if (find_target(p, address) != NULL) {
    pthread_mutex_unlock(&p->mu);
    producer_leave(p);
    return LIGHTNING_ERR_INVALID;
  }
  int slot = -1;
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    if (p->targets[i] == NULL) {
      slot = i;
      break;
    }
  }
  if (slot < 0) {
    pthread_mutex_unlock(&p->mu);
    producer_leave(p);
    return LIGHTNING_ERR_FULL;
  }
  target_t *t = calloc(1, sizeof(*t));
  if (t == NULL || (t->address = strdup(address)) == NULL) {
    free(t);
    pthread_mutex_unlock(&p->mu);
    producer_leave(p);
    return LIGHTNING_ERR_INTERNAL;
  }
  t->addr = addr;
  t->slot = (uint8_t)slot;
  t->connecting = true; /* keeps the connector thread off it */
  t->backoff_ms = BACKOFF_MIN_MS;
  p->targets[slot] = t;
  if (addr.shm) {
    p->shm_targets++;
  }
  pthread_mutex_unlock(&p->mu);

  /* One synchronous attempt, so a consumer that's already up is usable
   * as soon as we return; otherwise the connector thread retries. */
  pconn_t *c = producer_connect(p, address, &addr, (uint8_t)slot);
  pthread_mutex_lock(&p->mu);
  finish_connect(p, t, c);
  pthread_mutex_unlock(&p->mu);

  producer_leave(p);
  return LIGHTNING_OK;
}

lightning_error_t lightning_remove_target(lightning_producer_t *p,
                                          const char *address) {
  if (p == NULL || address == NULL) {
    return LIGHTNING_ERR_INVALID;
  }
  if (!producer_enter(p)) {
    return LIGHTNING_ERR_CLOSED;
  }
  /* Removing the last shm:// target resets the pool, which must not
   * race with a send publishing into it. */
  bool shm = strncmp(address, "shm://", 6) == 0;
  if (shm) {
    pthread_mutex_lock(&p->send_mu);
  }
  pthread_mutex_lock(&p->mu);
  target_t *t = find_target(p, address);
  pconn_t *c = NULL;
  if (t != NULL) {
    p->targets[t->slot] = NULL;
    t->removed = true;
    c = detach_conn(p, t);
    if (t->addr.shm && --p->shm_targets == 0) {
      pool_reset(p);
    }
    if (!t->connecting) {
      free_target(t); /* otherwise freed by whoever is connecting it */
    }
  }
  pthread_mutex_unlock(&p->mu);
  if (shm) {
    pthread_mutex_unlock(&p->send_mu);
  }
  if (c != NULL) {
    pconn_unref(c);
  }
  producer_leave(p);
  return t != NULL ? LIGHTNING_OK : LIGHTNING_ERR_INVALID;
}

lightning_error_t lightning_send(lightning_producer_t *p, const uint8_t *data,
                                 uint64_t data_size, uint32_t seq_num) {
  if (p == NULL || (data == NULL && data_size > 0)) {
    return LIGHTNING_ERR_INVALID;
  }
  if (!producer_enter(p)) {
    return LIGHTNING_ERR_CLOSED;
  }
  if (data_size > p->max_send_size) {
    producer_leave(p);
    return LIGHTNING_ERR_TOO_LARGE;
  }

  pthread_mutex_lock(&p->send_mu);
  pconn_t *conns[LIGHTNING_MAX_TARGETS];
  int n = 0;
  pthread_mutex_lock(&p->mu);
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    target_t *t = p->targets[i];
    if (t != NULL && t->conn != NULL) {
      atomic_fetch_add(&t->conn->refs, 1);
      conns[n++] = t->conn;
    }
  }
  bool any_shm = p->shm_targets > 0;
  pthread_mutex_unlock(&p->mu);

  bool published =
      any_shm && pool_publish(p, data, data_size, seq_num) >= 0;
  bool delivered = false;
  for (int i = 0; i < n; i++) {
    pconn_t *c = conns[i];
    if (c->ops->mode == MODE_UNBUFFERED && !published) {
      continue;
    }
    if (c->ops->offer(p, c, data, data_size, seq_num, &delivered) != 0) {
      conn_lost(p, c);
    }
  }
  pthread_mutex_unlock(&p->send_mu);

  for (int i = 0; i < n; i++) {
    pconn_unref(conns[i]);
  }
  producer_leave(p);
  return published || delivered ? LIGHTNING_OK : LIGHTNING_ERR_DROPPED;
}

lightning_message_t *lightning_recv_reply(lightning_producer_t *p,
                                          lightning_error_t *error) {
  if (p == NULL) {
    set_error(error, LIGHTNING_ERR_INVALID);
    return NULL;
  }
  if (!producer_enter(p)) {
    set_error(error, LIGHTNING_ERR_CLOSED);
    return NULL;
  }
  pthread_mutex_lock(&p->rq_mu);
  while (p->rq_head == NULL && !atomic_load(&p->closed)) {
    pthread_cond_wait(&p->rq_cond, &p->rq_mu);
  }
  reply_node_t *node = NULL;
  if (!atomic_load(&p->closed)) {
    node = p->rq_head;
    p->rq_head = node->next;
    if (p->rq_head == NULL) {
      p->rq_tail = NULL;
    }
  }
  pthread_mutex_unlock(&p->rq_mu);
  producer_leave(p);

  if (node == NULL) {
    set_error(error, LIGHTNING_ERR_CLOSED);
    return NULL;
  }
  lightning_message_t *msg = node->msg;
  free(node);
  set_error(error, LIGHTNING_OK);
  return msg;
}
