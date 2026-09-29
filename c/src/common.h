/* Gabriel common functions. */

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "gabriel_protocol.h"

#ifndef GABRIEL_COMMON_H
#define GABRIEL_COMMON_H

/* Sends exactly `len` bytes, retrying on a partial write or EINTR.
 * Returns false on error or disconnection. */
bool send_all(int fd, const void *buf, size_t len);

/* Receives exactly `len` bytes, retrying on a partial read or EINTR.
 * Returns false on error or disconnection. */
bool recv_all(int fd, void *buf, size_t len);

/* Passes `fd_to_send` to the peer on `sock` via SCM_RIGHTS. Returns
 * false on failure. */
bool send_fd(int sock, int fd);

/* Higher level message to send a gabriel_message_t over a socket. 
 * Returns an error on failure. */
gabriel_error_t send_message(int fd, gabriel_message_t *message);

/* Higher level message to receive a gabriel_message_t over a socket. 
 * Sets error and returns NULL on failure. */
gabriel_message_t *recv_message(int fd, gabriel_error_t *error);

/* Sets *error to value if error is non-NULL. */
void set_error(gabriel_error_t *error, gabriel_error_t value);

/* Logs to the Gabriel log callback. */
void gabriel_log(gabriel_log_level_t level, const char *fmt, ...);

#endif
