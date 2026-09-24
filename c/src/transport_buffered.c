#include <stdlib.h>

#include "core.h"

/* Buffered ("tcp://", "unix://"): frames and replies travel over the
 * socket as payloads. Frames are pushed whenever the consumer has a
 * token. */

static int buf_offer(lightning_producer_t *p, pconn_t *c,
                     const uint8_t *data, uint64_t size, uint32_t seq,
                     bool *delivered) {
  (void)p;
  pthread_mutex_lock(&c->mu);
  bool take = !c->dead && c->tokens > 0;
  if (take) {
    c->tokens--;
  }
  pthread_mutex_unlock(&c->mu);
  if (!take) {
    return 0; /* skipped for this frame */
  }

  wire_msg_t m = {.type = WIRE_FRAME,
                  .token = LIGHTNING_TOKEN_NONE,
                  .seq = seq,
                  .data_size = size,
                  .payload_size = size};
  pthread_mutex_lock(&c->wmu);
  int rc = wire_write(c->fd, &m, data);
  pthread_mutex_unlock(&c->wmu);
  if (rc != 0) {
    return -1;
  }
  *delivered = true;
  return 0;
}

static int buf_on_token(lightning_producer_t *p, pconn_t *c) {
  (void)p;
  (void)c;
  return 0; /* the refilled token is used by the next send */
}

static bool buf_take_reply(pconn_t *c, const wire_msg_t *m,
                           uint8_t *payload, uint8_t **data) {
  (void)c;
  if (m->payload_size != m->data_size) {
    free(payload);
    return false;
  }
  *data = payload;
  return true;
}

static void buf_on_lost(lightning_producer_t *p, pconn_t *c) {
  (void)p;
  (void)c;
}

static bool buf_take_frame(cconn_t *c, const wire_msg_t *m, uint8_t *payload,
                           uint8_t **data) {
  (void)c;
  if (m->payload_size != m->data_size) {
    free(payload);
    return false;
  }
  *data = payload;
  return true;
}

static void buf_drop_frame(cconn_t *c, const wire_msg_t *m,
                           uint8_t *payload) {
  (void)c;
  (void)m;
  free(payload);
}

static lightning_error_t buf_reply(lightning_consumer_t *k, cconn_t *c,
                                   const uint8_t *data, uint64_t size,
                                   uint32_t seq) {
  (void)k;
  wire_msg_t m = {.type = WIRE_REPLY,
                  .token = LIGHTNING_TOKEN_ACCEPT,
                  .seq = seq,
                  .data_size = size,
                  .payload_size = size};
  return wire_write(c->fd, &m, data) == 0 ? LIGHTNING_OK
                                          : LIGHTNING_ERR_BROKEN_PIPE;
}

static void buf_on_close(lightning_consumer_t *k, cconn_t *c) {
  (void)k;
  (void)c;
}

const transport_t transport_buffered = {
    .mode = MODE_BUFFERED,
    .offer = buf_offer,
    .on_token = buf_on_token,
    .take_reply = buf_take_reply,
    .on_lost = buf_on_lost,
    .take_frame = buf_take_frame,
    .drop_frame = buf_drop_frame,
    .reply = buf_reply,
    .on_close = buf_on_close,
};
