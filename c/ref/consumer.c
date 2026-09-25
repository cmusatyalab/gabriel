/* The consumer side of Lightning.
 *
 * Threads
 *   - Your thread calls lightning_recv() and lightning_reply(). Only it
 *     ever reads producer sockets or frees a producer connection.
 *   - The accept thread (accept_main) accepts new producers and runs the
 *     handshake, then hands each connection to the recv thread through
 *     the `handoff` list and rings wake_fd. It has to be a separate
 *     thread: a connecting producer waits for our half of the handshake,
 *     and your thread may be busy with a frame for a long time.
 *
 * lightning_destroy_consumer() may be called from any thread: it rings
 * stop_fd (accept thread) and wake_fd (a blocked recv), then waits until
 * no call is in progress before freeing anything.
 *
 * Tokens: every frame returned by lightning_recv() owes its producer
 * exactly one token back. lightning_reply() returns it with a REPLY. A
 * frame that is superseded before being returned, or that is never
 * replied to before the next lightning_recv(), returns it with a DROP. */

#include <errno.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <unistd.h>

#include "internal.h"

#define HANDSHAKE_TIMEOUT_MS 2000u

/* The consumer's connection to one producer. */
typedef struct cconn {
  int fd;
  bool shm;       /* unbuffered (shm://) */
  bool dead;      /* broken: freed by the next remove_dead() */
  hello_t peer;   /* the producer's handshake: name, ID, pool layout */
  char ip[INET_ADDRSTRLEN];
  wire_reader_t reader;
  lt_shm_t pool;  /* unbuffered: the producer's pool, mapped read-only */

  /* The newest frame that has arrived and not been returned yet. When a
   * newer one arrives, this one is dropped (its token goes back). */
  bool have_frame;
  wire_msg_t frame;
  uint8_t *payload; /* buffered: the frame's data */

  struct cconn *next_handoff; /* link in the accept thread's hand-off list */
} cconn_t;

struct lightning_consumer_t {
  hello_t self; /* our half of every handshake */
  lt_addr_t addr;
  int listen_fd;
  int wake_fd; /* eventfd: wakes recv (new producer, or destroy) */
  int stop_fd; /* eventfd: wakes the accept thread on destroy */
  pthread_t accept_thread;

  pthread_mutex_t mu;
  pthread_cond_t cond; /* destroy waits on it for active == 0 */
  _Atomic bool closed;
  int active;       /* API calls in progress (under mu) */
  cconn_t *handoff; /* handshaked, not yet adopted by recv (under mu) */

  /* Everything below belongs to the recv/reply thread. */
  cconn_t **conns;
  size_t num_conns;
  size_t cap_conns;
  size_t rr; /* round-robin: index of the producer to try first */

  /* The frame most recently returned by recv, until it's replied to. */
  bool pending;
  cconn_t *pending_conn; /* NULL if its producer has since gone away */
  uint32_t pending_seq;
  uint32_t pending_chunk;
};

/* ---- Small helpers ---- */

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

/* Returns a frame's token without a reply. */
static void send_drop(cconn_t *c, uint32_t seq, uint32_t chunk) {
  wire_msg_t m = {.type = WIRE_DROP, .seq = seq, .chunk = chunk};
  if (wire_write(c->fd, &m, NULL) != 0) {
    c->dead = true;
  }
}

static void cconn_free(cconn_t *c) {
  free(c->payload);
  wire_reader_free(&c->reader);
  shm_release(&c->pool);
  close(c->fd);
  free(c);
}

/* ---- New producers ---- */

/* Runs the consumer side of the handshake on a just-accepted socket:
 * read the producer's hello (and, for shm://, map its pool), then answer
 * with ours. Returns NULL (and closes `fd`) on failure. */
static cconn_t *consumer_handshake(lightning_consumer_t *k, int fd) {
  if (k->addr.family == AF_INET) {
    tcp_tune(fd);
  }
  /* A producer that stalls mid-handshake can hold up recv at most this
   * long. */
  set_io_timeout(fd, HANDSHAKE_TIMEOUT_MS);

  cconn_t *c = calloc(1, sizeof(*c));
  if (c == NULL) {
    close(fd);
    return NULL;
  }
  c->fd = fd;
  c->pool.fd = -1;

  if (hello_read(fd, &c->peer) != 0) {
    lightning_log(LIGHTNING_LOG_WARN,
                  "lightning: handshake with a new producer failed");
    goto fail;
  }
  c->shm = c->peer.mode == MODE_UNBUFFERED;
  if (c->peer.mode != MODE_BUFFERED &&
      !(c->shm && k->addr.family == AF_UNIX)) {
    /* Shared memory only works between processes on one machine. */
    lightning_log(LIGHTNING_LOG_WARN,
                  "lightning: rejected producer '%s' (shm:// needs a Unix "
                  "socket consumer)",
                  c->peer.name);
    hello_t nack = k->self;
    nack.ack = 0;
    hello_write(fd, &nack);
    goto fail;
  }
  if (c->shm) {
    int pool_fd = recv_fd(fd);
    if (pool_fd < 0 || shm_map(&c->pool, pool_fd, c->peer.chunk_count,
                               c->peer.chunk_stride) != 0) {
      lightning_log(LIGHTNING_LOG_WARN,
                    "lightning: failed to map the pool of '%s'", c->peer.name);
      goto fail;
    }
  }

  hello_t ack = k->self;
  ack.ack = HELLO_ACK;
  if (hello_write(fd, &ack) != 0) {
    goto fail;
  }
  set_io_timeout(fd, 0);

  peer_ip(fd, c->ip);
  /* Buffered frames arrive as payloads; unbuffered ones never have one. */
  c->reader.max_payload = c->shm ? 0 : c->peer.max_send_size;
  lightning_log(LIGHTNING_LOG_INFO, "lightning: producer '%s' connected (%s)",
                c->peer.name, c->shm ? "unbuffered" : "buffered");
  return c;

fail:
  shm_release(&c->pool);
  close(fd);
  free(c);
  return NULL;
}

static void signal_fd(int fd) {
  uint64_t one = 1;
  ssize_t n = write(fd, &one, sizeof(one));
  (void)n; /* EAGAIN just means a wakeup is already pending */
}

/* The accept thread: wait for a producer (or stop_fd), handshake it, and
 * hand it to the recv thread. */
static void *accept_main(void *arg) {
  lightning_consumer_t *k = arg;
  for (;;) {
    struct pollfd fds[2] = {{.fd = k->listen_fd, .events = POLLIN},
                            {.fd = k->stop_fd, .events = POLLIN}};
    if (poll(fds, 2, -1) < 0 && errno != EINTR) {
      lightning_log(LIGHTNING_LOG_ERROR, "lightning: poll() failed: %s",
                    strerror(errno));
      break;
    }
    if (atomic_load(&k->closed)) {
      break;
    }
    if (!(fds[0].revents & POLLIN)) {
      continue;
    }
    int fd = accept4(k->listen_fd, NULL, NULL, SOCK_CLOEXEC);
    if (fd < 0) {
      continue; /* spurious wakeup, or the producer already gave up */
    }
    cconn_t *c = consumer_handshake(k, fd);
    if (c == NULL) {
      continue;
    }
    /* Append (keeping arrival order) and wake the recv thread. */
    pthread_mutex_lock(&k->mu);
    cconn_t **tail = &k->handoff;
    while (*tail != NULL) {
      tail = &(*tail)->next_handoff;
    }
    *tail = c;
    pthread_mutex_unlock(&k->mu);
    signal_fd(k->wake_fd);
  }
  return NULL;
}

/* Moves producers the accept thread has handshaked into the recv
 * thread's own array. */
static lightning_error_t merge_handoff(lightning_consumer_t *k) {
  pthread_mutex_lock(&k->mu);
  cconn_t *list = k->handoff;
  k->handoff = NULL;
  pthread_mutex_unlock(&k->mu);

  lightning_error_t rc = LIGHTNING_OK;
  while (list != NULL) {
    cconn_t *c = list;
    list = c->next_handoff;
    if (k->num_conns == k->cap_conns) {
      size_t cap = k->cap_conns != 0 ? k->cap_conns * 2 : 8;
      cconn_t **conns = realloc(k->conns, cap * sizeof(*conns));
      if (conns == NULL) {
        cconn_free(c);
        rc = LIGHTNING_ERR_INTERNAL;
        continue;
      }
      k->conns = conns;
      k->cap_conns = cap;
    }
    k->conns[k->num_conns++] = c;
  }
  return rc;
}

/* ---- Receiving ---- */

/* Reads every frame waiting on `c`'s socket, keeping only the newest:
 * each older one is dropped and its token returned. Returns -1 if the
 * connection broke. */
static int pump(cconn_t *c) {
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
    if (c->have_frame) {
      /* Frames arrive in order, so the one we're holding is older. */
      send_drop(c, c->frame.seq, c->frame.chunk);
      free(c->payload);
    }
    c->have_frame = true;
    c->frame = m;
    c->payload = payload;
  }
}

/* Takes the data out of `c`'s held frame into a fresh buffer. Returns
 * false if the producer described the frame inconsistently. */
static bool take_frame(cconn_t *c, uint8_t **data) {
  const wire_msg_t *m = &c->frame;
  if (!c->shm) {
    if (m->payload_size != m->data_size) {
      return false;
    }
    *data = c->payload; /* adopt the buffer wire_read filled */
    c->payload = NULL;
    return true;
  }
  /* Unbuffered: the data is in the producer's pool. The producer won't
   * reuse this chunk until we return the frame's token, so it's safe to
   * read with no further checks. */
  if (m->payload_size != 0 || m->chunk >= c->pool.chunk_count ||
      m->data_size > c->pool.stride) {
    return false;
  }
  *data = NULL;
  if (m->data_size > 0) {
    *data = malloc((size_t)m->data_size);
    if (*data == NULL) {
      return false;
    }
    memcpy(*data, shm_chunk(&c->pool, m->chunk), (size_t)m->data_size);
  }
  return true;
}

/* Returns the next frame, trying producers in round-robin order, or NULL
 * if none has one. */
static lightning_message_t *pick_frame(lightning_consumer_t *k,
                                       lightning_error_t *error) {
  size_t n = k->num_conns;
  for (size_t i = 0; i < n; i++) {
    size_t idx = (k->rr + i) % n;
    cconn_t *c = k->conns[idx];
    if (c->dead || !c->have_frame) {
      continue;
    }
    c->have_frame = false;

    uint8_t *data = NULL;
    if (!take_frame(c, &data)) {
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: malformed frame from '%s'", c->peer.name);
      c->dead = true;
      continue;
    }
    lightning_message_t *msg =
        message_new(&c->peer, c->ip, c->frame.seq, data, c->frame.data_size);
    if (msg == NULL) {
      free(data);
      send_drop(c, c->frame.seq, c->frame.chunk);
      set_error(error, LIGHTNING_ERR_INTERNAL);
      return NULL;
    }
    k->rr = (idx + 1) % n; /* next time, start after this producer */
    k->pending = true;
    k->pending_conn = c;
    k->pending_seq = c->frame.seq;
    k->pending_chunk = c->frame.chunk;
    return msg;
  }
  return NULL;
}

/* Frees broken connections, keeping the round-robin position on the same
 * producer. */
static void remove_dead(lightning_consumer_t *k) {
  size_t kept = 0;
  size_t rr = k->rr;
  for (size_t i = 0; i < k->num_conns; i++) {
    cconn_t *c = k->conns[i];
    if (!c->dead) {
      k->conns[kept++] = c;
      continue;
    }
    lightning_log(LIGHTNING_LOG_INFO, "lightning: producer '%s' disconnected",
                  c->peer.name);
    if (k->pending_conn == c) {
      k->pending_conn = NULL; /* replying will report BROKEN_PIPE */
    }
    if (i < k->rr) {
      rr--;
    }
    cconn_free(c);
  }
  k->num_conns = kept;
  k->rr = rr < kept ? rr : 0;
}

/* Blocks until a producer socket or wake_fd (a new producer, or destroy)
 * is readable. */
static lightning_error_t wait_readable(lightning_consumer_t *k) {
  size_t n = k->num_conns;
  struct pollfd *fds = malloc((n + 1) * sizeof(*fds));
  if (fds == NULL) {
    return LIGHTNING_ERR_INTERNAL;
  }
  for (size_t i = 0; i < n; i++) {
    fds[i] = (struct pollfd){.fd = k->conns[i]->fd, .events = POLLIN};
  }
  fds[n] = (struct pollfd){.fd = k->wake_fd, .events = POLLIN};

  lightning_error_t rc = LIGHTNING_OK;
  if (poll(fds, (nfds_t)n + 1, -1) < 0 && errno != EINTR) {
    rc = LIGHTNING_ERR_INTERNAL;
  } else if (fds[n].revents & POLLIN) {
    uint64_t v;
    ssize_t r = read(k->wake_fd, &v, sizeof(v));
    (void)r;
  }
  free(fds);
  return rc;
}

/* ---- API ---- */

lightning_consumer_t *lightning_create_consumer(const char *source_name,
                                                const char *host_name,
                                                uint64_t max_send_size,
                                                const char *address,
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
  k->self.id = random_id();
  k->self.max_send_size = max_send_size;
  strcpy(k->self.name, source_name);
  strcpy(k->self.host, host_name != NULL ? host_name : "");
  k->addr = addr;
  k->wake_fd = -1;
  k->stop_fd = -1;
  pthread_mutex_init(&k->mu, NULL);
  pthread_cond_init(&k->cond, NULL);

  /* Non-blocking, so a producer that disconnects between the accept
   * thread's poll() and accept() can't leave accept() stuck. */
  k->listen_fd =
      socket(addr.family, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (k->listen_fd < 0) {
    goto fail;
  }
  if (addr.family == AF_INET) {
    /* Lets a restarted consumer bind its port again immediately, instead
     * of waiting out TIME_WAIT from its previous run. */
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
  pthread_mutex_destroy(&k->mu);
  pthread_cond_destroy(&k->cond);
  free(k);
  return NULL;
}

void lightning_destroy_consumer(lightning_consumer_t *k) {
  if (k == NULL) {
    return;
  }
  /* Wake the accept thread and a blocked recv, then wait for both. The
   * two eventfds are separate so neither thread can swallow the other's
   * wakeup. */
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
    cconn_free(k->conns[i]);
  }
  free(k->conns);
  while (k->handoff != NULL) {
    cconn_t *c = k->handoff;
    k->handoff = c->next_handoff;
    cconn_free(c);
  }
  close(k->listen_fd);
  if (k->addr.family == AF_UNIX) {
    unlink(k->addr.sa.un.sun_path);
  }
  close(k->wake_fd);
  close(k->stop_fd);
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

  /* The previous frame was never replied to: give its token back so the
   * producer isn't left waiting for it. */
  if (k->pending && k->pending_conn != NULL && !k->pending_conn->dead) {
    send_drop(k->pending_conn, k->pending_seq, k->pending_chunk);
  }
  k->pending = false;

  lightning_message_t *msg = NULL;
  lightning_error_t err = LIGHTNING_OK;
  while (!atomic_load(&k->closed)) {
    /* 1. Adopt new producers, then read what every producer has sent,
     *    keeping each one's newest frame. */
    err = merge_handoff(k);
    if (err != LIGHTNING_OK) {
      break;
    }
    for (size_t i = 0; i < k->num_conns; i++) {
      cconn_t *c = k->conns[i];
      if (!c->dead && pump(c) != 0) {
        c->dead = true;
      }
    }
    remove_dead(k);

    /* 2. Return the next producer's frame, if any has one. */
    msg = pick_frame(k, &err);
    if (msg != NULL || err != LIGHTNING_OK) {
      break;
    }
    remove_dead(k); /* a malformed frame may have killed a connection */

    /* 3. Nothing yet: sleep until something arrives, then go again. */
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
                                  uint64_t data_size) {
  if (k == NULL || (data == NULL && data_size > 0)) {
    return LIGHTNING_ERR_INVALID;
  }
  if (!consumer_enter(k)) {
    return LIGHTNING_ERR_CLOSED;
  }
  lightning_error_t rc = LIGHTNING_OK;
  cconn_t *c = k->pending_conn;
  if (!k->pending) {
    rc = LIGHTNING_ERR_INVALID; /* nothing received, or already replied */
  } else if (data_size > k->self.max_send_size) {
    rc = LIGHTNING_ERR_TOO_LARGE; /* still pending: a smaller reply works */
  } else if (c == NULL || c->dead) {
    k->pending = false;
    rc = LIGHTNING_ERR_BROKEN_PIPE;
  } else {
    k->pending = false;
    /* The reply carries the frame's chunk back so the producer knows
     * which chunk this consumer has finished with. */
    wire_msg_t m = {.type = WIRE_REPLY,
                    .seq = k->pending_seq,
                    .chunk = k->pending_chunk,
                    .data_size = data_size,
                    .payload_size = data_size};
    if (wire_write(c->fd, &m, data) != 0) {
      c->dead = true;
      rc = LIGHTNING_ERR_BROKEN_PIPE;
    }
  }
  consumer_leave(k);
  return rc;
}
