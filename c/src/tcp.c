#include "tcp.h"

#include <arpa/inet.h>
#include <endian.h>
#include <errno.h>
#include <netinet/tcp.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

#include "connection.h"
#include "internal.h"
#include "utils.h"

void tcp_set_congestion_control(int fd) {
  static const char *const algos[] = {"bbr2", "bbr"};
  for (size_t i = 0; i < sizeof(algos) / sizeof(algos[0]); i++) {
    if (setsockopt(fd, IPPROTO_TCP, TCP_CONGESTION, algos[i],
                   strlen(algos[i])) == 0) {
      return;
    }
  }
}

lightning_connection_t *tcp_connect_handshake(int fd, int max_tokens,
                                               lightning_error_t *error) {
  /* Handshake: declare our send budget and wait for the server to
   * acknowledge it before this connection is usable. */
  uint32_t wire_max_tokens = htonl((uint32_t)max_tokens);
  uint8_t ack = 0;
  if (send_all(fd, &wire_max_tokens, sizeof(wire_max_tokens)) != 0 ||
      recv_all(fd, &ack, sizeof(ack)) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "tcp_connect: handshake failed: %s", strerror(errno));
    set_error(error, LIGHTNING_ERR_BROKEN_PIPE);
    return NULL;
  }
  if (ack != LIGHTNING_ACK) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "tcp_connect: server sent an unexpected handshake ack");
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }

  struct lightning_connection_t *conn = malloc(sizeof(*conn));
  if (conn == NULL) {
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }
  memset(conn, 0, sizeof(*conn));
  conn->fd = fd;
  conn->family = AF_INET;
  conn->token_gated = true;
  conn->max_tokens = max_tokens;
  conn->num_tokens = 1; /* Start at one token, adapt up to max */
  return conn;
}

lightning_connection_t *tcp_accept_handshake(int fd,
                                              lightning_error_t *error) {
  uint32_t wire_max_tokens;
  uint8_t ack = LIGHTNING_ACK;
  if (recv_all(fd, &wire_max_tokens, sizeof(wire_max_tokens)) != 0 ||
      send_all(fd, &ack, sizeof(ack)) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "tcp_accept: handshake failed: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_BROKEN_PIPE);
    return NULL;
  }
  int max_tokens = (int)ntohl(wire_max_tokens);

  struct lightning_connection_t *conn = malloc(sizeof(*conn));
  if (conn == NULL) {
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }
  memset(conn, 0, sizeof(*conn));
  conn->fd = fd;
  conn->family = AF_INET;
  /* An accepted connection is never send-gated: it can reply whenever
   * it wants. Only the tcp_connect_handshake() side, which dictated
   * max_tokens as part of connecting, is bound by a send budget. */
  conn->token_gated = false;
  conn->max_tokens = max_tokens;
  conn->num_tokens = 0;
  return conn;
}

lightning_error_t tcp_send(lightning_connection_t *conn,
                            const lightning_message_t *msg) {
  if (conn->token_gated && conn->num_tokens <= 0) {
    return LIGHTNING_ERR_NO_TOKEN; /* No token available, drop */
  }

  size_t source_name_len =
      msg->source_name != NULL ? strlen(msg->source_name) : 0;
  uint32_t wire_source_name_len = htonl((uint32_t)source_name_len);
  uint8_t wire_token = msg->token ? 1 : 0;
  uint64_t wire_meta_size = htobe64(msg->meta_size);
  uint64_t wire_data_size = htobe64(msg->data_size);

  if (send_all(conn->fd, &wire_source_name_len,
               sizeof(wire_source_name_len)) != 0 ||
      send_all(conn->fd, msg->source_name, source_name_len) != 0 ||
      send_all(conn->fd, &wire_token, sizeof(wire_token)) != 0 ||
      send_all(conn->fd, &wire_meta_size, sizeof(wire_meta_size)) != 0 ||
      send_all(conn->fd, msg->meta, msg->meta_size) != 0 ||
      send_all(conn->fd, &wire_data_size, sizeof(wire_data_size)) != 0 ||
      send_all(conn->fd, msg->data, msg->data_size) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "tcp_send: failed to write message: %s",
                   strerror(errno));
    return LIGHTNING_ERR_BROKEN_PIPE;
  }

  if (conn->token_gated) {
    conn->num_tokens--;
  }
  return LIGHTNING_OK;
}

lightning_error_t tcp_recv(lightning_connection_t *conn,
                            lightning_message_t *msg) {
  uint32_t wire_source_name_len;
  if (recv_all(conn->fd, &wire_source_name_len,
               sizeof(wire_source_name_len)) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "tcp_recv: failed to read message header: %s",
                   strerror(errno));
    return LIGHTNING_ERR_BROKEN_PIPE;
  }
  uint32_t source_name_len = ntohl(wire_source_name_len);
  char *source_name = malloc((size_t)source_name_len + 1);
  if (source_name == NULL) {
    return LIGHTNING_ERR_INTERNAL;
  }
  if (recv_all(conn->fd, source_name, source_name_len) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "tcp_recv: failed to read source_name: %s",
                   strerror(errno));
    free(source_name);
    return LIGHTNING_ERR_BROKEN_PIPE;
  }
  source_name[source_name_len] = '\0';

  uint8_t wire_token;
  uint64_t wire_meta_size;
  uint64_t wire_data_size;
  if (recv_all(conn->fd, &wire_token, sizeof(wire_token)) != 0 ||
      recv_all(conn->fd, &wire_meta_size, sizeof(wire_meta_size)) != 0 ||
      recv_all(conn->fd, &wire_data_size, sizeof(wire_data_size)) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "tcp_recv: failed to read message header: %s",
                   strerror(errno));
    free(source_name);
    return LIGHTNING_ERR_BROKEN_PIPE;
  }
  uint64_t meta_size = be64toh(wire_meta_size);
  uint64_t data_size = be64toh(wire_data_size);

  int8_t *meta = NULL;
  if (meta_size > 0) {
    meta = malloc(meta_size);
    if (meta == NULL) {
      free(source_name);
      return LIGHTNING_ERR_INTERNAL;
    }
  }
  int8_t *data = NULL;
  if (data_size > 0) {
    data = malloc(data_size);
    if (data == NULL) {
      free(source_name);
      free(meta);
      return LIGHTNING_ERR_INTERNAL;
    }
  }

  if ((meta_size > 0 && recv_all(conn->fd, meta, meta_size) != 0) ||
      (data_size > 0 && recv_all(conn->fd, data, data_size) != 0)) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "tcp_recv: failed to read message payload: %s",
                   strerror(errno));
    free(source_name);
    free(meta);
    free(data);
    return LIGHTNING_ERR_BROKEN_PIPE;
  }

  msg->source_name = source_name;
  msg->token = wire_token != 0;
  msg->meta_size = meta_size;
  msg->meta = meta;
  msg->data_size = data_size;
  msg->data = data;

  if (msg->token && conn->token_gated && conn->num_tokens < conn->max_tokens) {
    conn->num_tokens++;
  }
  return LIGHTNING_OK;
}
