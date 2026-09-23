#ifndef GABRIEL_H
#define GABRIEL_H

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
 * this client's flow-control budget. */
GabrielClient *gabriel_new_client(const char *address, int num_tokens);

/* Starts a Gabriel server listening at `address`. */
GabrielServer *gabriel_new_server(const char *address);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GABRIEL_H */
