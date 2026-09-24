#include <poll.h>
#include <stdlib.h>
#include <string.h>

#include "core.h"

/* Unbuffered ("shm://"): frame and reply data live in shared memory and
 * only chunk indices go over the socket. A consumer is handed the
 * newest frame whenever it has a token. See README.md, "Unbuffered
 * connections", for the chunk header protocol.
 *
 * Reads of a chunk are validated seqlock-style: the header is checked
 * before and after copying, so a chunk that was reclaimed mid-copy
 * (only possible once the peer considers this connection dead) is
 * discarded rather than returned torn. */

/* ---- Producer chunk pool ---- */

int pool_publish(lightning_producer_t *p, const uint8_t *data, uint64_t size,
                 uint32_t seq) {
  const lt_shm_t *pool = &p->pool;
  uint32_t best;
  for (;;) {
    int found = -1;
    uint32_t found_seq = 0;
    uint64_t found_h = 0;
    for (uint32_t i = 0; i < pool->chunk_count; i++) {
      uint64_t h =
          atomic_load_explicit(shm_hdr(pool, i), memory_order_acquire);
      if (h & (POOL_LOCK | POOL_MASK_ALL)) {
        continue; /* being written, or still being read */
      }
      if (!atomic_load_explicit(&p->valid[i], memory_order_relaxed)) {
        found = (int)i; /* never used: best possible choice */
        found_h = h;
        break;
      }
      /* Reuse the oldest frame, keeping the newest ones available. */
      if (found < 0 || seq_gt(found_seq, POOL_SEQ(h))) {
        found = (int)i;
        found_seq = POOL_SEQ(h);
        found_h = h;
      }
    }
    if (found < 0) {
      return -1; /* every chunk is being read */
    }
    uint64_t h = found_h;
    if (atomic_compare_exchange_strong_explicit(
            shm_hdr(pool, (uint32_t)found), &h, h | POOL_LOCK,
            memory_order_acq_rel, memory_order_acquire)) {
      best = (uint32_t)found;
      break;
    }
    /* A consumer was just handed that chunk; pick another. */
  }

  atomic_thread_fence(memory_order_release);
  if (size > 0) {
    memcpy(shm_data(pool, best), data, (size_t)size);
  }
  atomic_store_explicit(&p->sizes[best], size, memory_order_relaxed);
  atomic_store_explicit(&p->valid[best], true, memory_order_relaxed);
  /* Publish: mask 0, lock clear, new seq. */
  atomic_store_explicit(shm_hdr(pool, best), (uint64_t)seq,
                        memory_order_release);
  atomic_store_explicit(&p->latest_seq, seq, memory_order_relaxed);
  atomic_store_explicit(&p->have_published, true, memory_order_release);
  return (int)best;
}

void pool_clear_slot(lightning_producer_t *p, uint8_t slot) {
  for (uint32_t i = 0; i < p->pool.chunk_count; i++) {
    atomic_fetch_and_explicit(shm_hdr(&p->pool, i), ~POOL_BIT(slot),
                              memory_order_acq_rel);
  }
}

void pool_reset(lightning_producer_t *p) {
  shm_punch(&p->pool);
  for (uint32_t i = 0; i < p->pool.chunk_count; i++) {
    atomic_store(&p->valid[i], false);
    atomic_store(&p->sizes[i], 0);
  }
  atomic_store(&p->have_published, false);
}

/* Hands `c` the newest frame it hasn't seen, while it has tokens.
 * c->mu must be held. Returns -1 if the connection broke. */
static int shm_dispatch(lightning_producer_t *p, pconn_t *c,
                        bool *delivered) {
  const lt_shm_t *pool = &p->pool;
  const uint64_t bit = POOL_BIT(c->slot);

  while (!c->dead && c->tokens > 0) {
    int best = -1;
    uint32_t best_seq = 0;
    uint64_t best_h = 0;
    for (uint32_t i = 0; i < pool->chunk_count; i++) {
      if (!atomic_load_explicit(&p->valid[i], memory_order_acquire)) {
        continue;
      }
      uint64_t h =
          atomic_load_explicit(shm_hdr(pool, i), memory_order_acquire);
      if ((h & POOL_LOCK) || (h & bit)) {
        continue;
      }
      uint32_t s = POOL_SEQ(h);
      if (c->have_last_seq && !seq_gt(s, c->last_seq)) {
        continue;
      }
      if (best < 0 || seq_gt(s, best_seq)) {
        best = (int)i;
        best_seq = s;
        best_h = h;
      }
    }
    if (best < 0) {
      return 0; /* nothing newer yet: waits for the next send */
    }

    /* Set this consumer's bit, as long as the chunk still holds the
     * same, unlocked frame. Other consumers' bits may change under us,
     * so retry on those. */
    _Atomic uint64_t *hdr = shm_hdr(pool, (uint32_t)best);
    uint64_t h = best_h;
    bool claimed = false;
    while (!(h & POOL_LOCK) && POOL_SEQ(h) == best_seq && !(h & bit)) {
      if (atomic_compare_exchange_weak_explicit(hdr, &h, h | bit,
                                                memory_order_acq_rel,
                                                memory_order_acquire)) {
        claimed = true;
        break;
      }
    }
    if (!claimed) {
      continue; /* the chunk was taken for reuse; rescan */
    }

    uint64_t size = atomic_load_explicit(&p->sizes[best], memory_order_relaxed);
    c->tokens--;
    c->have_last_seq = true;
    c->last_seq = best_seq;

    wire_msg_t m = {.type = WIRE_FRAME,
                    .token = LIGHTNING_TOKEN_NONE,
                    .seq = best_seq,
                    .chunk = (uint32_t)best,
                    .data_size = size,
                    .payload_size = 0};
    pthread_mutex_lock(&c->wmu);
    int rc = wire_write(c->fd, &m, NULL);
    pthread_mutex_unlock(&c->wmu);
    if (rc != 0) {
      return -1;
    }
    if (delivered != NULL) {
      *delivered = true;
    }
  }
  return 0;
}

static int shm_offer(lightning_producer_t *p, pconn_t *c, const uint8_t *data,
                     uint64_t size, uint32_t seq, bool *delivered) {
  (void)data;
  (void)size;
  (void)seq;
  pthread_mutex_lock(&c->mu);
  int rc = shm_dispatch(p, c, delivered);
  pthread_mutex_unlock(&c->mu);
  return rc;
}

static int shm_on_token(lightning_producer_t *p, pconn_t *c) {
  return shm_dispatch(p, c, NULL);
}

static bool shm_take_reply(pconn_t *c, const wire_msg_t *m, uint8_t *payload,
                           uint8_t **data) {
  free(payload); /* never sent for unbuffered */
  const lt_shm_t *area = &c->reply_area;
  if (m->payload_size != 0 || m->chunk >= area->chunk_count ||
      m->data_size > area->stride - LT_CHUNK_HDR) {
    return false;
  }
  _Atomic uint64_t *hdr = shm_hdr(area, m->chunk);
  uint64_t h1 = atomic_load_explicit(hdr, memory_order_acquire);
  if (REPLY_OWNER(h1) == 0 || POOL_SEQ(h1) != m->seq) {
    return false;
  }

  uint8_t *copy = NULL;
  if (m->data_size > 0) {
    copy = malloc((size_t)m->data_size);
    if (copy == NULL) {
      return false;
    }
    memcpy(copy, shm_data(area, m->chunk), (size_t)m->data_size);
  }
  atomic_thread_fence(memory_order_acquire);
  uint64_t h2 = atomic_load_explicit(hdr, memory_order_relaxed);

  /* Free the chunk for the consumer's next reply. */
  if (h1 != h2 || !atomic_compare_exchange_strong_explicit(
                      hdr, &h1, 0, memory_order_release,
                      memory_order_relaxed)) {
    free(copy);
    return false;
  }
  *data = copy;
  return true;
}

static void shm_on_lost(lightning_producer_t *p, pconn_t *c) {
  pool_clear_slot(p, c->slot);
}

/* ---- Consumer ---- */

static bool frame_hdr_ok(uint64_t h, uint64_t bit, uint32_t seq) {
  return !(h & POOL_LOCK) && (h & bit) && POOL_SEQ(h) == seq;
}

/* Clears this consumer's bit, if the chunk still holds `seq`. */
static void release_frame(cconn_t *c, uint32_t chunk, uint32_t seq) {
  if (chunk >= c->pool.chunk_count) {
    return;
  }
  const uint64_t bit = POOL_BIT(c->peer.slot);
  _Atomic uint64_t *hdr = shm_hdr(&c->pool, chunk);
  uint64_t h = atomic_load_explicit(hdr, memory_order_relaxed);
  while (frame_hdr_ok(h, bit, seq)) {
    if (atomic_compare_exchange_weak_explicit(hdr, &h, h & ~bit,
                                              memory_order_release,
                                              memory_order_relaxed)) {
      return;
    }
  }
}

static bool shm_take_frame(cconn_t *c, const wire_msg_t *m, uint8_t *payload,
                           uint8_t **data) {
  free(payload);
  const lt_shm_t *pool = &c->pool;
  if (m->payload_size != 0 || m->chunk >= pool->chunk_count ||
      m->data_size > pool->stride - LT_CHUNK_HDR) {
    return false;
  }
  const uint64_t bit = POOL_BIT(c->peer.slot);
  _Atomic uint64_t *hdr = shm_hdr(pool, m->chunk);
  if (!frame_hdr_ok(atomic_load_explicit(hdr, memory_order_acquire), bit,
                    m->seq)) {
    return false;
  }

  uint8_t *copy = NULL;
  if (m->data_size > 0) {
    copy = malloc((size_t)m->data_size);
    if (copy == NULL) {
      release_frame(c, m->chunk, m->seq);
      return false;
    }
    memcpy(copy, shm_data(pool, m->chunk), (size_t)m->data_size);
  }
  atomic_thread_fence(memory_order_acquire);
  if (!frame_hdr_ok(atomic_load_explicit(hdr, memory_order_relaxed), bit,
                    m->seq)) {
    free(copy);
    return false;
  }
  release_frame(c, m->chunk, m->seq);
  *data = copy;
  return true;
}

static void shm_drop_frame(cconn_t *c, const wire_msg_t *m,
                           uint8_t *payload) {
  free(payload);
  release_frame(c, m->chunk, m->seq);
}

static lightning_error_t shm_reply(lightning_consumer_t *k, cconn_t *c,
                                   const uint8_t *data, uint64_t size,
                                   uint32_t seq) {
  lt_shm_t *area = &k->reply_area;
  const uint64_t claimed_h = REPLY_HDR(c->owner_id, seq);
  unsigned backoff_us = 10;
  int chunk = -1;
  while (chunk < 0) {
    for (uint32_t i = 0; i < area->chunk_count; i++) {
      _Atomic uint64_t *hdr = shm_hdr(area, i);
      uint64_t h = atomic_load_explicit(hdr, memory_order_acquire);
      if (REPLY_OWNER(h) == 0 &&
          atomic_compare_exchange_strong_explicit(hdr, &h, claimed_h,
                                                  memory_order_acquire,
                                                  memory_order_relaxed)) {
        chunk = (int)i;
        break;
      }
    }
    if (chunk >= 0) {
      break;
    }
    /* Every chunk is waiting on a producer to copy it out. Wait, but
     * give up if we're closing or this producer is gone. */
    if (atomic_load(&k->closed)) {
      return LIGHTNING_ERR_CLOSED;
    }
    struct pollfd pfd = {.fd = c->fd, .events = POLLRDHUP};
    if (poll(&pfd, 1, 0) > 0 &&
        (pfd.revents & (POLLHUP | POLLERR | POLLRDHUP | POLLNVAL))) {
      return LIGHTNING_ERR_BROKEN_PIPE;
    }
    sleep_us(backoff_us);
    if (backoff_us < 1000) {
      backoff_us *= 2;
    }
  }

  _Atomic uint64_t *hdr = shm_hdr(area, (uint32_t)chunk);
  if (size > 0) {
    memcpy(shm_data(area, (uint32_t)chunk), data, (size_t)size);
  }
  atomic_store_explicit(hdr, claimed_h, memory_order_release);
  k->reply_area_dirty = true;

  wire_msg_t m = {.type = WIRE_REPLY,
                  .token = LIGHTNING_TOKEN_ACCEPT,
                  .seq = seq,
                  .chunk = (uint32_t)chunk,
                  .data_size = size,
                  .payload_size = 0};
  if (wire_write(c->fd, &m, NULL) != 0) {
    atomic_store_explicit(hdr, 0, memory_order_release);
    return LIGHTNING_ERR_BROKEN_PIPE;
  }
  return LIGHTNING_OK;
}

static void shm_on_close(lightning_consumer_t *k, cconn_t *c) {
  /* Free every reply chunk this producer never collected. */
  if (k->has_reply_area) {
    for (uint32_t i = 0; i < k->reply_area.chunk_count; i++) {
      _Atomic uint64_t *hdr = shm_hdr(&k->reply_area, i);
      uint64_t h = atomic_load_explicit(hdr, memory_order_relaxed);
      if (REPLY_OWNER(h) == c->owner_id) {
        atomic_compare_exchange_strong(hdr, &h, 0);
      }
    }
  }
  shm_release(&c->pool);
}

const transport_t transport_unbuffered = {
    .mode = MODE_UNBUFFERED,
    .offer = shm_offer,
    .on_token = shm_on_token,
    .take_reply = shm_take_reply,
    .on_lost = shm_on_lost,
    .take_frame = shm_take_frame,
    .drop_frame = shm_drop_frame,
    .reply = shm_reply,
    .on_close = shm_on_close,
};
