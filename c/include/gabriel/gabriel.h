#ifndef GABRIEL_H
#define GABRIEL_H

#include <stddef.h>

#include "gabriel/errors.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Returns the library version string, e.g. "0.1.0". */
const char *gabriel_version(void);

/* Placeholder call demonstrating the binding plumbing end to end.
 * Replace with real API functions. */
int gabriel_add(int a, int b);

/* GabrielClient/GabrielServer are the protocol layer, built on top of
 * the Lightning transport (see lightning.h). Opaque: callers only
 * ever hold a pointer. */
typedef struct GabrielClient GabrielClient;
typedef struct GabrielServer GabrielServer;

/* Connects to a Gabriel server at `address`, with `num_tokens` as
 * this client's flow-control budget and `shm_size` as the requested
 * shared memory size for a "unix://" address (ignored for "tcp://";
 * pass 0 for the transport's default). `error`, if non-NULL, is set
 * to the specific reason on failure (LIGHTNING_OK on success); pass
 * NULL if you don't need it. */
GabrielClient *gabriel_new_client(const char *address, int num_tokens,
                                   size_t shm_size, lightning_error_t *error);

/* Starts a Gabriel server listening at `address`. `error` follows the
 * same convention as gabriel_new_client()'s. */
GabrielServer *gabriel_new_server(const char *address,
                                   lightning_error_t *error);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GABRIEL_H */
