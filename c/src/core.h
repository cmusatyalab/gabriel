#ifndef LIGHTNING_CORE_H
#define LIGHTNING_CORE_H

#include <pthread.h>

#include "internal.h"

/* Producer/consumer state shared between producer.c, consumer.c and
 * the two transports (transport_buffered.c, transport_unbuffered.c).
 *
 * Producer lock order: send_mu -> mu -> pconn.mu -> pconn.wmu. The
 * reply queue's rq_mu is a leaf. */

typedef struct pconn pconn_t;
typedef struct cconn cconn_t;

/* One implementation per connection mode. Producer-side operations
 * take a pconn (the producer's connection to a consumer), consumer-side
 * ones a cconn (the consumer's connection to a producer). */
typedef struct transport {
  uint8_t mode; /* MODE_BUFFERED or MODE_UNBUFFERED */

  /* Producer: offers a just-sent frame to this consumer. Buffered
   * pushes it if the consumer has a token, unbuffered hands out the
   * newest published frame if it has one (`data` is unused). Returns
   * -1 if the connection broke. */
  int (*offer)(lightning_producer_t *p, pconn_t *c, const uint8_t *data,
               uint64_t size, uint32_t seq, bool *delivered);
  /* Producer: a token came back (c->mu held). Unbuffered hands out a
   * newer frame if there is one. Returns -1 if the connection broke. */
  int (*on_token)(lightning_producer_t *p, pconn_t *c);
  /* Producer: turns a WIRE_REPLY into the reply's data. Buffered adopts
   * `payload`; unbuffered copies it out of the consumer's reply area
   * and frees the chunk. Returns false if the reply was unreadable. */
  bool (*take_reply)(pconn_t *c, const wire_msg_t *m, uint8_t *payload,
                     uint8_t **data);
  /* Producer: connection lost or removed (p->mu held). */
  void (*on_lost)(lightning_producer_t *p, pconn_t *c);

  /* Consumer: turns a queued WIRE_FRAME into the frame's data, as
   * above. Returns false if the frame was unreadable. */
  bool (*take_frame)(cconn_t *c, const wire_msg_t *m, uint8_t *payload,
                     uint8_t **data);
  /* Consumer: discards a queued frame without reading it. */
  void (*drop_frame)(cconn_t *c, const wire_msg_t *m, uint8_t *payload);
  /* Consumer: sends a reply. Returns LIGHTNING_OK or an error. */
  lightning_error_t (*reply)(lightning_consumer_t *k, cconn_t *c,
                             const uint8_t *data, uint64_t size,
                             uint32_t seq);
  /* Consumer: connection being freed. */
  void (*on_close)(lightning_consumer_t *k, cconn_t *c);
} transport_t;

extern const transport_t transport_buffered;
extern const transport_t transport_unbuffered;

/* ---- Producer ---- */

struct pconn {
  _Atomic int refs;
  const transport_t *ops;
  int fd;
  uint8_t slot;
  hello_t peer;
  char ip[INET_ADDRSTRLEN];

  /* Token and dispatch state, protected by mu. */
  pthread_mutex_t mu;
  bool dead;
  uint32_t tokens;
  bool have_token_seq;
  uint32_t token_seq; /* highest seq_num that returned a token */
  bool have_last_seq;
  uint32_t last_seq; /* unbuffered: highest seq_num handed out */

  /* Serializes writes to fd. */
  pthread_mutex_t wmu;

  /* Only touched by the reader thread. */
  wire_reader_t reader;

  /* Unbuffered: the consumer's reply area. */
  lt_shm_t reply_area;
};

typedef struct target {
  char *address;
  lt_addr_t addr;
  uint8_t slot;
  bool removed;
  bool connecting; /* a connect attempt is in progress outside p->mu */
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
  uint32_t stale_seqs;
  uint64_t id;
  char name[LT_NAME_MAX + 1];
  char host[LT_NAME_MAX + 1];

  pthread_mutex_t mu;
  pthread_cond_t cond; /* connector wakeups, destroy waiting on active */
  target_t *targets[LIGHTNING_MAX_TARGETS]; /* indexed by bit slot */
  int shm_targets;
  _Atomic bool closed;
  int active; /* API calls in progress */

  pthread_mutex_t send_mu;

  /* Unbuffered chunk pool. `valid[i]` is set once chunk i holds a
   * frame (reset when the pool is punched), `sizes[i]` is its data
   * size. Both are producer-local. */
  lt_shm_t pool;
  _Atomic bool *valid;
  _Atomic uint64_t *sizes;
  _Atomic bool have_published;
  _Atomic uint32_t latest_seq;

  int wake_fd; /* eventfd waking the reader thread */
  pthread_t reader_thread;
  pthread_t connector_thread;

  pthread_mutex_t rq_mu;
  pthread_cond_t rq_cond;
  reply_node_t *rq_head;
  reply_node_t *rq_tail;
};

void pconn_unref(pconn_t *c);

/* Chunk pool helpers (transport_unbuffered.c). pool_publish() writes a
 * frame into the oldest free chunk and returns its index, or -1 if
 * every chunk is being read (send_mu held). pool_clear_slot() clears a
 * slot's bit on every chunk. pool_reset() releases the pool's memory
 * (send_mu and mu held, no unbuffered connections left). */
int pool_publish(lightning_producer_t *p, const uint8_t *data, uint64_t size,
                 uint32_t seq);
void pool_clear_slot(lightning_producer_t *p, uint8_t slot);
void pool_reset(lightning_producer_t *p);

/* ---- Consumer ---- */

typedef struct frame_node {
  wire_msg_t msg;
  uint8_t *payload;
  struct frame_node *next;
} frame_node_t;

struct cconn {
  const transport_t *ops;
  int fd;
  uint32_t owner_id; /* nonzero; tags this producer's reply chunks */
  hello_t peer;
  char ip[INET_ADDRSTRLEN];
  bool dead;
  wire_reader_t reader;
  frame_node_t *head;
  frame_node_t *tail;
  bool have_newest;
  uint32_t newest_seq;

  /* Unbuffered: the producer's chunk pool. */
  lt_shm_t pool;

  struct cconn *next_pending;
};

struct lightning_consumer_t {
  uint64_t id;
  uint64_t max_send_size;
  char name[LT_NAME_MAX + 1];
  char host[LT_NAME_MAX + 1];
  lt_addr_t addr;
  int listen_fd;

  /* Reply area for unbuffered producers (Unix socket addresses only).
   * Only the recv/reply thread claims chunks. */
  bool has_reply_area;
  lt_shm_t reply_area;
  bool reply_area_dirty;

  pthread_mutex_t mu;
  pthread_cond_t cond;
  cconn_t *pending; /* accepted, not yet picked up by recv */
  _Atomic bool closed;
  int active;
  int wake_fd; /* eventfd waking recv (new producer, or destroy) */
  int stop_fd; /* eventfd waking the accept thread on destroy */
  pthread_t accept_thread;
  uint32_t next_owner_id; /* accept thread only */

  /* recv/reply thread only. */
  cconn_t **conns;
  size_t num_conns;
  size_t cap_conns;
  size_t rr;
  cconn_t *last_sender;
  bool had_recv;
};

#endif /* LIGHTNING_CORE_H */
