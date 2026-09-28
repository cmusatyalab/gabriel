#include "gabriel/gabriel.h"

#include <stdlib.h>

/* TODO: real fields once these are wired up to the protocol layer. */
struct GabrielSource {
  int unused;
};

struct GabrielEngine {
  int unused;
};

struct GabrielSink {
  int unused;
};

struct GabrielServer {
  int unused;
};

struct GabrielClient {
  int unused;
};

GabrielSource *gabriel_new_source(void) {
  return calloc(1, sizeof(GabrielSource));
}

GabrielEngine *gabriel_new_engine(void) {
  return calloc(1, sizeof(GabrielEngine));
}

GabrielSink *gabriel_new_sink(void) {
  return calloc(1, sizeof(GabrielSink));
}

GabrielServer *gabriel_new_server(void) {
  return calloc(1, sizeof(GabrielServer));
}

GabrielClient *gabriel_new_client(void) {
  return calloc(1, sizeof(GabrielClient));
}
