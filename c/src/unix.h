#ifndef GABRIEL_UNIX_H
#define GABRIEL_UNIX_H

#include "gabriel/lightning.h"

/* Completes the client side of the handshake on an already-connect()'d
 * AF_UNIX `fd`: exchanges max_tokens/shm_size, then creates and swaps
 * a shared memory arena with the peer in each direction. Returns the
 * resulting connection, or NULL on failure. */
lightning_connection_t *unix_connect_handshake(int fd, int max_tokens,
                                                size_t shm_size,
                                                lightning_error_t *error);

/* Completes the server side of the handshake on an already-accept()'d
 * AF_UNIX `fd`. Returns the resulting connection, or NULL on failure. */
lightning_connection_t *unix_accept_handshake(int fd,
                                               lightning_error_t *error);

lightning_error_t unix_send(lightning_connection_t *conn,
                             const lightning_message_t *msg);
lightning_error_t unix_recv(lightning_connection_t *conn,
                             lightning_message_t *msg);

int unix_shared_memory(const lightning_connection_t *conn, int *fd,
                        size_t *size);

#endif /* GABRIEL_UNIX_H */
