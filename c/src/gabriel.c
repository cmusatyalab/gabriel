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

GabrielClient *gabriel_new_client(const char *address, int num_tokens) {
  GabrielClient *client = malloc(sizeof(*client));
  if (client == NULL) {
    return NULL;
  }
  client->conn = lightning_connect(address, num_tokens);
  if (client->conn == NULL) {
    free(client);
    return NULL;
  }
  return client;
}

GabrielServer *gabriel_new_server(const char *address) {
  GabrielServer *server = malloc(sizeof(*server));
  if (server == NULL) {
    return NULL;
  }
  server->listener = lightning_bind(address);
  if (server->listener == NULL) {
    free(server);
    return NULL;
  }
  return server;
}
