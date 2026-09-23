#ifndef GABRIEL_INTERNAL_H
#define GABRIEL_INTERNAL_H

#include "gabriel/lightning.h"

/* Byte the server sends back once it has stored the client's declared
 * max_tokens, so lightning_connect() knows the handshake succeeded. */
#define LIGHTNING_ACK 0xACu

/* Default shared memory arena size, used when lightning_connect() is
 * asked for 0 instead of a specific size. */
#define LIGHTNING_SHM_SIZE ((size_t)(4 * 1024 * 1024))

/* Calls the callback registered with lightning_set_log_callback(), if
 * any; a no-op otherwise. Shared by lightning.c/tcp.c/unix.c. */
void lightning_log(lightning_log_level_t level, const char *fmt, ...);

/* Sets *error to value if error is non-NULL. */
void set_error(lightning_error_t *error, lightning_error_t value);

#endif /* GABRIEL_INTERNAL_H */
