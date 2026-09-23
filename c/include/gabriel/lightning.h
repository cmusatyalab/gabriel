#ifndef GABRIEL_LIGHTNING_H
#define GABRIEL_LIGHTNING_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Lightning is the low-level transport: connect/bind/send/recv plus
 * per-connection token-based flow control. The gabriel_* API (see
 * gabriel.h) is the protocol layer built on top of it. */

typedef struct lightning_connection lightning_connection_t;

/* A listener created by lightning_bind(), distinct from a connection:
 * it accepts new connections via lightning_accept() but is never
 * itself something you send/recv on. */
typedef struct lightning_listener lightning_listener_t;

typedef enum {
  LIGHTNING_MESSAGE_DATA = 0,
  LIGHTNING_MESSAGE_HEARTBEAT = 1,
  LIGHTNING_MESSAGE_RESULT_OR_TOKEN = 2,
} lightning_message_type_t;

/* A single message on the wire. `payload`/`payload_len` are unused
 * for LIGHTNING_MESSAGE_HEARTBEAT. Frame/producer identifiers are a
 * protocol-layer concern and are expected to live inside `payload`,
 * not here. */
typedef struct {
  lightning_message_type_t type;
  const uint8_t *payload;
  size_t payload_len;
} lightning_message_t;

/* Connects to a Lightning listener at `address` (an "ip:port" or a
 * Unix domain socket path). `num_tokens` is this connection's flow
 * control budget: send() drops a message if none are available. */
lightning_connection_t *lightning_connect(const char *address,
                                           int num_tokens);

/* Starts listening at `address` for incoming connections. */
lightning_listener_t *lightning_bind(const char *address);

/* Blocks until a new connection arrives on `listener`, returning it.
 * Each accepted connection is independent, with its own token count,
 * buffer, and heartbeat state - callers accept in a loop to serve
 * more than one at a time. */
lightning_connection_t *lightning_accept(lightning_listener_t *listener);

/* Sends `msg`. Returns 0 if sent, or a negative value if dropped
 * because no token was available. */
int lightning_send(lightning_connection_t *conn,
                    const lightning_message_t *msg);

/* Blocks until a message arrives, filling in `msg`. Returns 0 on
 * success, or a negative value on error/disconnection. */
int lightning_recv(lightning_connection_t *conn, lightning_message_t *msg);

/* Returns a token to `conn`, replenishing its send budget by one. */
void lightning_return_token(lightning_connection_t *conn);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GABRIEL_LIGHTNING_H */
