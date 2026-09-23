#ifndef GABRIEL_TCP_H
#define GABRIEL_TCP_H

#include "gabriel/lightning.h"

/* Sets the best available TCP congestion control algorithm on `fd`.
 * A no-op-on-failure best effort - leaves the OS default in place if
 * nothing better is loaded. */
void tcp_set_congestion_control(int fd);

/* Completes the client side of the handshake on an already-connect()'d
 * `fd` and returns the resulting connection, or NULL on failure. */
lightning_connection_t *tcp_connect_handshake(int fd, int max_tokens,
                                               lightning_error_t *error);

/* Completes the server side of the handshake on an already-accept()'d
 * `fd` and returns the resulting connection, or NULL on failure. */
lightning_connection_t *tcp_accept_handshake(int fd, lightning_error_t *error);

lightning_error_t tcp_send(lightning_connection_t *conn,
                            const lightning_message_t *msg);
lightning_error_t tcp_recv(lightning_connection_t *conn,
                            lightning_message_t *msg);

#endif /* GABRIEL_TCP_H */
