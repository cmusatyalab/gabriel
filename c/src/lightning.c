#include "gabriel/lightning.h"

#include <stdlib.h>

/* TODO: replace with the real fields once the transport is designed
 * (socket fd, token count, ring buffer / overwrite slot, heartbeat
 * timers, etc.). Lifecycle management (closing/freeing a connection)
 * is also intentionally not implemented yet. */
struct lightning_connection {
  int num_tokens;
};

/* TODO: replace with the real fields once the transport is designed
 * (listening socket fd, etc.). */
struct lightning_listener {
  int unused;
};

lightning_connection_t *lightning_connect(const char *address,
                                           int num_tokens) {
  (void)address; /* TODO: actually connect */
  struct lightning_connection *conn = malloc(sizeof(*conn));
  if (conn == NULL) {
    return NULL;
  }
  conn->num_tokens = num_tokens;
  return conn;
}

lightning_listener_t *lightning_bind(const char *address) {
  (void)address; /* TODO: actually listen */
  struct lightning_listener *listener = malloc(sizeof(*listener));
  return listener;
}

lightning_connection_t *lightning_accept(lightning_listener_t *listener) {
  (void)listener;
  /* TODO: block until a new connection arrives. A server-accepted
   * connection doesn't get num_tokens from the caller; the client
   * dictates its own budget as part of connecting (see
   * lightning_connect), so this always starts at 0 until that's
   * wired up. */
  struct lightning_connection *conn = malloc(sizeof(*conn));
  if (conn == NULL) {
    return NULL;
  }
  conn->num_tokens = 0;
  return conn;
}

int lightning_send(lightning_connection_t *conn,
                    const lightning_message_t *msg) {
  (void)conn;
  (void)msg;
  /* TODO: drop and return < 0 if no token is available, otherwise
   * write the frame to the wire. */
  return -1;
}

int lightning_recv(lightning_connection_t *conn, lightning_message_t *msg) {
  (void)conn;
  (void)msg;
  /* TODO: block until a frame arrives. */
  return -1;
}

void lightning_return_token(lightning_connection_t *conn) {
  if (conn == NULL) {
    return;
  }
  conn->num_tokens++;
}
