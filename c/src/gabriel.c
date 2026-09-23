#include "gabriel/gabriel.h"

#include <stdlib.h>

#include "gabriel/lightning.h"

const char *gabriel_version(void) { return "0.1.0"; }

int gabriel_add(int a, int b) { return a + b; }

struct GabrielClient {
  lightning_connection_t *conn;
};

struct GabrielServer {
  lightning_listener_t *listener;
};

GabrielClient *gabriel_new_client(const char *address, int num_tokens,
                                   size_t shm_size, lightning_error_t *error) {
  GabrielClient *client = malloc(sizeof(*client));
  if (client == NULL) {
    if (error != NULL) {
      *error = LIGHTNING_ERR_INTERNAL;
    }
    return NULL;
  }
  client->conn = lightning_connect(address, num_tokens, shm_size, error);
  if (client->conn == NULL) {
    free(client);
    return NULL;
  }
  return client;
}

GabrielServer *gabriel_new_server(const char *address,
                                   lightning_error_t *error) {
  GabrielServer *server = malloc(sizeof(*server));
  if (server == NULL) {
    if (error != NULL) {
      *error = LIGHTNING_ERR_INTERNAL;
    }
    return NULL;
  }
  server->listener = lightning_bind(address, error);
  if (server->listener == NULL) {
    free(server);
    return NULL;
  }
  return server;
}
