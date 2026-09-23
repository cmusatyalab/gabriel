#ifndef GABRIEL_CONNECTION_H
#define GABRIEL_CONNECTION_H

#include "gabriel/lightning.h"

/* Shared definition of the opaque lightning_connection_t/
 * lightning_listener_t types - tcp.c and unix.c each populate and use
 * only the fields relevant to their transport. */

struct lightning_connection_t {
  int fd;
  int family; /* AF_INET or AF_UNIX */

  /* AF_INET only. */
  bool token_gated;
  int max_tokens;
  int num_tokens;

  /* AF_UNIX only: the arena this side writes into, and whether this
   * side currently holds the write-lock for it (see unix.c). */
  int send_arena_fd;
  void *send_arena_addr;
  size_t send_arena_size;
  bool have_send_lock;

  /* AF_UNIX only: the arena this side reads from. */
  int recv_arena_fd;
  void *recv_arena_addr;
  size_t recv_arena_size;

  /* AF_UNIX only: a header already read off the wire (while checking
   * for a returned lock) whose payload hasn't been fetched from
   * recv_arena_addr by lightning_recv() yet. */
  bool has_pending_header;
  char *pending_source_name;
  bool pending_token;
  uint64_t pending_meta_size;
  uint64_t pending_data_size;

  long long seq_num; /* unused so far */
};

struct lightning_listener_t {
  int fd;
  int family; /* AF_INET or AF_UNIX */
};

#endif /* GABRIEL_CONNECTION_H */
