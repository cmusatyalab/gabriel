/* The producer side of Lightning.
 *
 * Threads
 *   - Your threads call lightning_send / recv_reply / add_target /
 *     remove_target.
 *   - The reader thread (reader_main) polls every connection. Replies go
 *     onto a queue for lightning_recv_reply(); every REPLY or DROP gives
 *     a token back, and for an unbuffered consumer that immediately hands
 *     it the newest frame.
 *   - The connector thread (connector_main) retries targets whose
 *     consumer isn't reachable.
 *
 * Locks, always taken in this order:
 *   send_mu   serializes lightning_send(), so there's one pool writer.
 *   mu        the target table and each target's current connection.
 *   state_mu  every connection's tokens and `dead` flag, the shared
 *             memory pool's bookkeeping, and writes to unbuffered
 *             sockets. Held only briefly.
 *   rq_mu     the reply queue (never held with the others).
 *
 * Connections (pconn_t) are reference counted: the target holds one
 * reference, and send / the reader take a temporary one while they use
 * it, so removing a target never frees a connection out from under
 * them. */

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <time.h>
#include <unistd.h>

#include "internal.h"

#define CONNECT_TIMEOUT_MS 1000u
#define HANDSHAKE_TIMEOUT_MS 2000u
#define BACKOFF_MIN_MS 50u
#define BACKOFF_MAX_MS 1000u

/* The producer's connection to one consumer. */
typedef struct pconn {
  _Atomic int refs;
  int fd;
  bool shm;                /* unbuffered (shm://) */
  hello_t peer;            /* the consumer's handshake: name, ID, ... */
  char ip[INET_ADDRSTRLEN];
  wire_reader_t reader;    /* only touched by the reader thread */

  /* Protected by state_mu. */
  bool dead;               /* detached: never hand it anything again */
  uint32_t tokens;         /* frames it can still be sent */
  uint64_t last_gen;       /* unbuffered: pool generation last handed */
  uint32_t *held;          /* unbuffered: chunks it holds (<= max_tokens) */
  uint32_t num_held;
} pconn_t;

/* An address the producer sends to, connected or not. */
typedef struct target {
  char *address;
  lt_addr_t addr;
  bool removed;    /* removed while a connect attempt was in flight */
  bool connecting; /* a connect attempt is in progress outside mu */
  pconn_t *conn;   /* NULL while disconnected */
  uint64_t next_attempt_ms;
  unsigned backoff_ms;
} target_t;

typedef struct reply_node {
  lightning_message_t *msg;
  struct reply_node *next;
} reply_node_t;

struct lightning_producer_t {
  uint32_t max_tokens;
  uint64_t max_send_size;
  hello_t self; /* our handshake: name, host, ID, pool layout */

  pthread_mutex_t send_mu;

  pthread_mutex_t mu;
  pthread_cond_t cond; /* wakes the connector; destroy waits on it too */
  target_t *targets[LIGHTNING_MAX_TARGETS];
  _Atomic bool closed;
  int active; /* API calls in progress (under mu) */

  /* The shared memory pool, all under state_mu. A chunk is free when
   * nobody holds it (refs == 0) and it isn't `latest`: the newest frame
   * is never overwritten, so there is always one to hand out. `gens`
   * numbers every publish, which is how dispatch tells whether a
   * consumer has already seen the newest frame. */
  pthread_mutex_t state_mu;
  lt_shm_t pool;
  uint32_t *refs;  /* consumers holding each chunk (+1 while written) */
  uint32_t *seqs;  /* seq_num of the frame in each chunk */
  uint64_t *sizes; /* data size of the frame in each chunk */
  uint64_t *gens;  /* publish generation of each chunk */
  int latest;      /* chunk with the newest frame, -1 before the first */
  uint64_t next_gen;

  int wake_fd; /* eventfd: wakes the reader to re-read the target table */
  pthread_t reader_thread;
  pthread_t connector_thread;

  pthread_mutex_t rq_mu;
  pthread_cond_t rq_cond;
  reply_node_t *rq_head;
  reply_node_t *rq_tail;
};

/* ---- Small helpers ---- */

/* Every API call is bracketed by enter/leave so destroy can wait until
 * nothing is using the producer before freeing it. */
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

static void pconn_ref(pconn_t *c) { atomic_fetch_add(&c->refs, 1); }

static void pconn_unref(pconn_t *c) {
  if (atomic_fetch_sub(&c->refs, 1) != 1) {
    return;
  }
  close(c->fd);
  wire_reader_free(&c->reader);
  free(c->held);
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

/* Takes a reference to every connected consumer. mu held. */
static int snapshot_conns(lightning_producer_t *p, pconn_t **out) {
  int n = 0;
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    target_t *t = p->targets[i];
    if (t != NULL && t->conn != NULL) {
      pconn_ref(t->conn);
      out[n++] = t->conn;
    }
  }
  return n;
}

/* ---- Shared memory pool (all with state_mu held) ---- */

/* Picks the lowest-index free chunk for a new frame and holds it while
 * it's written. Always taking the lowest index means only as many
 * chunks as are in use at once ever get touched, so only they cost
 * memory. Returns -1 if every chunk is held. */
static int pool_claim(lightning_producer_t *p) {
  for (uint32_t i = 0; i < p->pool.chunk_count; i++) {
    if (p->refs[i] == 0 && (int)i != p->latest) {
      p->refs[i] = 1; /* the writer's hold */
      return (int)i;
    }
  }
  return -1;
}

/* Hands `c` the newest frame, if it has a token and hasn't been given
 * that frame yet. Returns -1 if the socket write failed. */
static int dispatch(lightning_producer_t *p, pconn_t *c) {
  if (c->dead || c->tokens == 0 || p->latest < 0 ||
      p->gens[p->latest] == c->last_gen) {
    return 0;
  }
  uint32_t i = (uint32_t)p->latest;
  /* Only a 32-byte header, so writing it under state_mu is cheap. It
   * can't block for long either: the consumer holds at most max_tokens
   * frames, so its socket never has more than that many headers queued. */
  wire_msg_t m = {.type = WIRE_FRAME,
                  .seq = p->seqs[i],
                  .chunk = i,
                  .data_size = p->sizes[i]};
  if (wire_write(c->fd, &m, NULL) != 0) {
    return -1;
  }
  p->refs[i]++;
  c->held[c->num_held++] = i;
  c->tokens--;
  c->last_gen = p->gens[i];
  return 0;
}

/* Releases the hold `c` has on `chunk`. Returns false if it doesn't hold
 * it, which means the consumer broke the protocol. */
static bool release_held(lightning_producer_t *p, pconn_t *c, uint32_t chunk) {
  for (uint32_t k = 0; k < c->num_held; k++) {
    if (c->held[k] == chunk) {
      c->held[k] = c->held[--c->num_held];
      p->refs[chunk]--;
      return true;
    }
  }
  return false;
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
  return fcntl(fd, F_SETFL, flags); /* back to blocking */
}

/* Connects to `addr` and runs the producer side of the handshake: send
 * our hello (plus the pool's memfd for shm://), then read the consumer's
 * answer. Only reads fields of `p` that never change, so it runs without
 * any lock. Returns a new connection with a full token bucket. */
static pconn_t *producer_connect(lightning_producer_t *p, const char *address,
                                 const lt_addr_t *addr) {
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

  pconn_t *c = calloc(1, sizeof(*c));
  if (c == NULL || (c->held = calloc(p->max_tokens, sizeof(*c->held))) == NULL) {
    free(c);
    close(fd);
    return NULL;
  }

  hello_t h = p->self;
  h.mode = addr->shm ? MODE_UNBUFFERED : MODE_BUFFERED;
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
                  "lightning: '%s' rejected the connection (shm:// needs a "
                  "Unix socket consumer)",
                  address);
    goto fail;
  }
  set_io_timeout(fd, 0);

  atomic_init(&c->refs, 1);
  c->fd = fd;
  c->shm = addr->shm;
  peer_ip(fd, c->ip);
  c->tokens = p->max_tokens;
  /* Replies always arrive as payloads, up to the consumer's max. */
  c->reader.max_payload = c->peer.max_send_size;
  lightning_log(LIGHTNING_LOG_INFO, "lightning: connected to '%s' (%s)",
                address, addr->shm ? "unbuffered" : "buffered");
  return c;

fail:
  close(fd);
  free(c->held);
  free(c);
  return NULL;
}

/* Detaches `t`'s connection: marks it dead, releases every chunk it held
 * and shuts its socket down (which also unblocks a send stuck writing to
 * it). mu held. Returns the connection so the caller can drop the
 * target's reference after unlocking. */
static pconn_t *detach_conn(lightning_producer_t *p, target_t *t) {
  pconn_t *c = t->conn;
  if (c == NULL) {
    return NULL;
  }
  t->conn = NULL;
  pthread_mutex_lock(&p->state_mu);
  c->dead = true;
  while (c->num_held > 0) {
    release_held(p, c, c->held[0]);
  }
  pthread_mutex_unlock(&p->state_mu);
  shutdown(c->fd, SHUT_RDWR);
  return c;
}

/* Completes a connect attempt made outside mu. mu held. */
static void finish_connect(lightning_producer_t *p, target_t *t, pconn_t *c) {
  t->connecting = false;
  if (t->removed || atomic_load(&p->closed)) {
    /* Nobody wants this connection any more. A removed target was left
     * for us to free, since we were the ones using it. */
    if (t->removed) {
      free_target(t);
    }
    if (c != NULL) {
      pconn_unref(c);
    }
    return;
  }
  if (c == NULL) {
    /* Unreachable: let the connector retry after a doubling backoff. */
    t->next_attempt_ms = now_ms() + t->backoff_ms;
    t->backoff_ms = t->backoff_ms * 2 < BACKOFF_MAX_MS ? t->backoff_ms * 2
                                                       : BACKOFF_MAX_MS;
    pthread_cond_broadcast(&p->cond);
    return;
  }
  /* A new unbuffered consumer must never be handed a frame published
   * before it connected, so it starts as having seen the newest one. */
  pthread_mutex_lock(&p->state_mu);
  c->last_gen = p->latest >= 0 ? p->gens[p->latest] : 0;
  pthread_mutex_unlock(&p->state_mu);
  t->conn = c;
  t->backoff_ms = BACKOFF_MIN_MS;
  wake_reader(p); /* start polling the new socket */
}

/* Handles a broken connection, found by send or the reader. */
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
      pthread_cond_broadcast(&p->cond); /* wake the connector */
      break;
    }
  }
  pthread_mutex_unlock(&p->mu);
  if (detached != NULL) {
    pconn_unref(detached);
  }
}

/* The connector thread: connects any target that is disconnected and
 * due for a retry, then sleeps until the next one is due. */
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
      /* Connecting can take seconds: do it without the lock. `connecting`
       * keeps everyone else off this target meanwhile. */
      pick->connecting = true;
      lt_addr_t addr = pick->addr;
      char *address = strdup(pick->address);
      pthread_mutex_unlock(&p->mu);
      pconn_t *c = address != NULL ? producer_connect(p, address, &addr) : NULL;
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

/* A REPLY or DROP gave back the token for one frame. For an unbuffered
 * consumer that also releases the frame's chunk and hands it the newest
 * frame straight away. Returns -1 if the connection should be dropped. */
static int return_token(lightning_producer_t *p, pconn_t *c,
                        const wire_msg_t *m) {
  int rc = 0;
  pthread_mutex_lock(&p->state_mu);
  if (!c->dead) {
    if (c->shm && !release_held(p, c, m->chunk)) {
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: '%s' returned a chunk it doesn't hold",
                    c->peer.name);
      rc = -1;
    } else {
      if (c->tokens < p->max_tokens) {
        c->tokens++;
      }
      if (c->shm) {
        rc = dispatch(p, c);
      }
    }
  }
  pthread_mutex_unlock(&p->state_mu);
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

    lightning_message_t *msg = NULL;
    if (m.type == WIRE_REPLY) {
      msg = message_new(&c->peer, c->ip, m.seq, payload, m.payload_size);
      if (msg == NULL) {
        free(payload);
      }
    } else if (m.type == WIRE_DROP) {
      free(payload);
    } else {
      free(payload);
      lightning_log(LIGHTNING_LOG_ERROR,
                    "lightning: unexpected message type %u from '%s'",
                    (unsigned)m.type, c->peer.name);
      return -1;
    }

    /* Give the token back before queueing the reply, so a caller that
     * got the reply from lightning_recv_reply() can rely on the token
     * being back. */
    if (return_token(p, c, &m) != 0) {
      lightning_message_free(msg);
      return -1;
    }
    if (msg != NULL) {
      push_reply(p, msg);
    }
  }
}

/* The reader thread: snapshot the connections, wait until one of them
 * (or the wake eventfd) is readable, process it, repeat. */
static void *reader_main(void *arg) {
  lightning_producer_t *p = arg;
  pconn_t *conns[LIGHTNING_MAX_TARGETS];
  struct pollfd fds[LIGHTNING_MAX_TARGETS + 1];

  for (;;) {
    pthread_mutex_lock(&p->mu);
    if (atomic_load(&p->closed)) {
      pthread_mutex_unlock(&p->mu);
      break;
    }
    int n = snapshot_conns(p, conns);
    pthread_mutex_unlock(&p->mu);

    for (int i = 0; i < n; i++) {
      fds[i] = (struct pollfd){.fd = conns[i]->fd, .events = POLLIN};
    }
    fds[n] = (struct pollfd){.fd = p->wake_fd, .events = POLLIN};

    if (poll(fds, (nfds_t)n + 1, -1) < 0 && errno != EINTR) {
      lightning_log(LIGHTNING_LOG_ERROR, "lightning: poll() failed: %s",
                    strerror(errno));
    }
    if (fds[n].revents & POLLIN) {
      /* Targets changed (or we're closing): drain the eventfd and go
       * round again with a fresh snapshot. */
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

lightning_producer_t *lightning_create_producer(uint32_t max_tokens,
                                                uint64_t max_send_size,
                                                const char *source_name,
                                                const char *host_name,
                                                const char **targets,
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
  p->latest = -1;
  p->next_gen = 1; /* 0 means "nothing seen yet" */
  p->wake_fd = -1;
  p->pool.fd = -1;

  pthread_condattr_t attr;
  pthread_condattr_init(&attr);
  pthread_condattr_setclock(&attr, CLOCK_MONOTONIC); /* for timed waits */
  pthread_mutex_init(&p->send_mu, NULL);
  pthread_mutex_init(&p->mu, NULL);
  pthread_cond_init(&p->cond, &attr);
  pthread_condattr_destroy(&attr);
  pthread_mutex_init(&p->state_mu, NULL);
  pthread_mutex_init(&p->rq_mu, NULL);
  pthread_cond_init(&p->rq_cond, NULL);

  /* The pool needs room for every consumer to hold max_tokens different
   * chunks, plus `latest`, plus the one being written. It's only address
   * space: pages get memory when written, and pool_claim() keeps reusing
   * the lowest chunks. */
  uint32_t chunks = max_tokens * LIGHTNING_MAX_TARGETS + 2;
  if (max_tokens > (UINT32_MAX - 2) / LIGHTNING_MAX_TARGETS ||
      shm_create(&p->pool, chunks, max_send_size) != 0 ||
      (p->refs = calloc(chunks, sizeof(*p->refs))) == NULL ||
      (p->seqs = calloc(chunks, sizeof(*p->seqs))) == NULL ||
      (p->sizes = calloc(chunks, sizeof(*p->sizes))) == NULL ||
      (p->gens = calloc(chunks, sizeof(*p->gens))) == NULL ||
      (p->wake_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK)) < 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                  "lightning: failed to allocate producer resources: %s",
                  strerror(errno));
    goto fail;
  }

  /* Our half of every handshake. */
  p->self.id = random_id();
  p->self.max_send_size = max_send_size;
  p->self.chunk_count = p->pool.chunk_count;
  p->self.chunk_stride = p->pool.stride;
  strcpy(p->self.name, source_name);
  strcpy(p->self.host, host_name != NULL ? host_name : "");

  if (pthread_create(&p->reader_thread, NULL, reader_main, p) != 0) {
    goto fail;
  }
  if (pthread_create(&p->connector_thread, NULL, connector_main, p) != 0) {
    atomic_store(&p->closed, true);
    wake_reader(p);
    pthread_join(p->reader_thread, NULL);
    goto fail;
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

fail:
  set_error(error, LIGHTNING_ERR_INTERNAL);
  if (p->wake_fd >= 0) {
    close(p->wake_fd);
  }
  free(p->refs);
  free(p->seqs);
  free(p->sizes);
  free(p->gens);
  shm_release(&p->pool);
  pthread_mutex_destroy(&p->send_mu);
  pthread_mutex_destroy(&p->mu);
  pthread_cond_destroy(&p->cond);
  pthread_mutex_destroy(&p->state_mu);
  pthread_mutex_destroy(&p->rq_mu);
  pthread_cond_destroy(&p->rq_cond);
  free(p);
  return NULL;
}

void lightning_destroy_producer(lightning_producer_t *p) {
  if (p == NULL) {
    return;
  }
  /* 1. Mark closed and wake everything that might be blocked. */
  pthread_mutex_lock(&p->mu);
  atomic_store(&p->closed, true);
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    if (p->targets[i] != NULL && p->targets[i]->conn != NULL) {
      shutdown(p->targets[i]->conn->fd, SHUT_RDWR); /* unblocks a send */
    }
  }
  pthread_cond_broadcast(&p->cond);
  pthread_mutex_unlock(&p->mu);
  wake_reader(p);
  pthread_mutex_lock(&p->rq_mu);
  pthread_cond_broadcast(&p->rq_cond);
  pthread_mutex_unlock(&p->rq_mu);

  /* 2. Wait for in-flight API calls and the threads to finish. */
  pthread_mutex_lock(&p->mu);
  while (p->active > 0) {
    pthread_cond_wait(&p->cond, &p->mu);
  }
  pthread_mutex_unlock(&p->mu);
  pthread_join(p->reader_thread, NULL);
  pthread_join(p->connector_thread, NULL);

  /* 3. Nothing else can touch the producer now: free everything. */
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
  free(p->refs);
  free(p->seqs);
  free(p->sizes);
  free(p->gens);
  shm_release(&p->pool);
  pthread_mutex_destroy(&p->send_mu);
  pthread_mutex_destroy(&p->mu);
  pthread_cond_destroy(&p->cond);
  pthread_mutex_destroy(&p->state_mu);
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
  lightning_error_t rc = LIGHTNING_OK;
  int slot = -1;
  for (int i = 0; i < LIGHTNING_MAX_TARGETS && slot < 0; i++) {
    if (p->targets[i] == NULL) {
      slot = i;
    }
  }
  target_t *t = NULL;
  if (find_target(p, address) != NULL) {
    rc = LIGHTNING_ERR_INVALID;
  } else if (slot < 0) {
    rc = LIGHTNING_ERR_FULL;
  } else if ((t = calloc(1, sizeof(*t))) == NULL ||
             (t->address = strdup(address)) == NULL) {
    free(t);
    rc = LIGHTNING_ERR_INTERNAL;
  } else {
    t->addr = addr;
    t->connecting = true; /* keeps the connector thread off it for now */
    t->backoff_ms = BACKOFF_MIN_MS;
    p->targets[slot] = t;
  }
  pthread_mutex_unlock(&p->mu);

  if (rc == LIGHTNING_OK) {
    /* One synchronous attempt, so a consumer that's already up is usable
     * as soon as we return; otherwise the connector thread retries. */
    pconn_t *c = producer_connect(p, address, &addr);
    pthread_mutex_lock(&p->mu);
    finish_connect(p, t, c);
    pthread_mutex_unlock(&p->mu);
  }
  producer_leave(p);
  return rc;
}

lightning_error_t lightning_remove_target(lightning_producer_t *p,
                                          const char *address) {
  if (p == NULL || address == NULL) {
    return LIGHTNING_ERR_INVALID;
  }
  if (!producer_enter(p)) {
    return LIGHTNING_ERR_CLOSED;
  }
  pthread_mutex_lock(&p->mu);
  target_t *t = find_target(p, address);
  pconn_t *c = NULL;
  if (t != NULL) {
    for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
      if (p->targets[i] == t) {
        p->targets[i] = NULL;
      }
    }
    t->removed = true;
    c = detach_conn(p, t);
    if (!t->connecting) {
      free_target(t); /* otherwise finish_connect() frees it */
    }
  }
  pthread_mutex_unlock(&p->mu);
  if (c != NULL) {
    pconn_unref(c);
  }
  wake_reader(p); /* stop polling the removed socket */
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
  bool broken[LIGHTNING_MAX_TARGETS] = {false};
  pthread_mutex_lock(&p->mu);
  int n = snapshot_conns(p, conns);
  pthread_mutex_unlock(&p->mu);

  bool any_shm = false;
  for (int k = 0; k < n; k++) {
    any_shm |= conns[k]->shm;
  }

  /* Unbuffered consumers: write the frame once into the pool, make it
   * the newest, and hand it to every consumer that has a token. The
   * others get it (or something newer) when their token comes back. */
  bool published = false;
  if (any_shm) {
    pthread_mutex_lock(&p->state_mu);
    int i = pool_claim(p);
    pthread_mutex_unlock(&p->state_mu);
    if (i >= 0) {
      if (data_size > 0) {
        memcpy(shm_chunk(&p->pool, (uint32_t)i), data, (size_t)data_size);
      }
      pthread_mutex_lock(&p->state_mu);
      p->refs[i] = 0; /* drop the writer's hold */
      p->seqs[i] = seq_num;
      p->sizes[i] = data_size;
      p->gens[i] = p->next_gen++;
      p->latest = i;
      for (int k = 0; k < n; k++) {
        if (conns[k]->shm && dispatch(p, conns[k]) != 0) {
          broken[k] = true;
        }
      }
      pthread_mutex_unlock(&p->state_mu);
      published = true;
    }
  }

  /* Buffered consumers: push the frame to each one that has a token. The
   * write happens outside state_mu, so a slow TCP consumer never stops
   * the reader thread from returning other consumers' tokens. */
  bool delivered = false;
  for (int k = 0; k < n; k++) {
    pconn_t *c = conns[k];
    if (c->shm) {
      continue;
    }
    pthread_mutex_lock(&p->state_mu);
    bool take = !c->dead && c->tokens > 0;
    if (take) {
      c->tokens--;
    }
    pthread_mutex_unlock(&p->state_mu);
    if (!take) {
      continue; /* no token: this consumer skips this frame */
    }
    wire_msg_t m = {.type = WIRE_FRAME,
                    .seq = seq_num,
                    .data_size = data_size,
                    .payload_size = data_size};
    if (wire_write(c->fd, &m, data) != 0) {
      broken[k] = true;
    } else {
      delivered = true;
    }
  }

  for (int k = 0; k < n; k++) {
    if (broken[k]) {
      conn_lost(p, conns[k]);
    }
    pconn_unref(conns[k]);
  }
  pthread_mutex_unlock(&p->send_mu);
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
