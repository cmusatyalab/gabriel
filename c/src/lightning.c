#include "gabriel/lightning.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "connection.h"
#include "internal.h"
#include "tcp.h"
#include "unix.h"

static lightning_log_fn g_log_fn = NULL;
static void *g_log_user_data = NULL;

void lightning_set_log_callback(lightning_log_fn fn, void *user_data) {
  g_log_fn = fn;
  g_log_user_data = user_data;
}

void lightning_log(lightning_log_level_t level, const char *fmt, ...) {
  if (g_log_fn == NULL) {
    return;
  }
  char message[256];
  va_list args;
  va_start(args, fmt);
  vsnprintf(message, sizeof(message), fmt, args);
  va_end(args);
  g_log_fn(level, message, g_log_user_data);
}

void set_error(lightning_error_t *error, lightning_error_t value) {
  if (error != NULL) {
    *error = value;
  }
}

/* Holds whichever concrete sockaddr type parse_address() produced. */
typedef union {
  struct sockaddr_in inet;
  struct sockaddr_un un;
} raw_address_t;

/* Parses "tcp://host:port" into a sockaddr_in, or "unix://path" into
 * a sockaddr_un. Fills `family` (AF_INET or AF_UNIX) and `len` (the
 * resulting sockaddr's size) and returns 0 on success. */
static int parse_address(const char *address, raw_address_t *addr,
                          int *family, socklen_t *len) {
  static const char kTcpPrefix[] = "tcp://";
  static const char kUnixPrefix[] = "unix://";

  if (strncmp(address, kTcpPrefix, strlen(kTcpPrefix)) == 0) {
    const char *host_port = address + strlen(kTcpPrefix);
    const char *colon = strrchr(host_port, ':');
    if (colon == NULL) {
      return -1;
    }

    char host[256];
    size_t host_len = (size_t)(colon - host_port);
    if (host_len >= sizeof(host)) {
      return -1;
    }
    memcpy(host, host_port, host_len);
    host[host_len] = '\0';

    memset(&addr->inet, 0, sizeof(addr->inet));
    addr->inet.sin_family = AF_INET;
    addr->inet.sin_port = htons((uint16_t)atoi(colon + 1));
    if (inet_pton(AF_INET, host, &addr->inet.sin_addr) != 1) {
      return -1;
    }
    *family = AF_INET;
    *len = sizeof(addr->inet);
    return 0;
  } else if (strncmp(address, kUnixPrefix, strlen(kUnixPrefix)) == 0) {
    const char *path = address + strlen(kUnixPrefix);
    if (strlen(path) >= sizeof(addr->un.sun_path)) {
      return -1;
    }

    memset(&addr->un, 0, sizeof(addr->un));
    addr->un.sun_family = AF_UNIX;
    strcpy(addr->un.sun_path, path);
    *family = AF_UNIX;
    *len = sizeof(addr->un);
    return 0;
  }

  /* Unknown address prefix */
  return -1;
}

lightning_connection_t *lightning_connect(const char *address, int max_tokens,
                                           size_t shm_size,
                                           lightning_error_t *error) {
  set_error(error, LIGHTNING_OK);

  raw_address_t addr;
  int family;
  socklen_t len;
  if (parse_address(address, &addr, &family, &len) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "lightning_connect: invalid address '%s'", address);
    set_error(error, LIGHTNING_ERR_INVALID);
    return NULL;
  }

  int fd = socket(family, SOCK_STREAM, 0);
  if (fd < 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "lightning_connect: socket() failed: %s", strerror(errno));
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }
  if (family == AF_INET) { /* Only use congestion control for TCP */
    tcp_set_congestion_control(fd);
  }

  if (connect(fd, (struct sockaddr *)&addr, len) != 0) {
    int err = errno;
    lightning_log(LIGHTNING_LOG_ERROR,
                   "lightning_connect: connect() to '%s' failed: %s", address,
                   strerror(err));
    set_error(error, LIGHTNING_ERR_INTERNAL);
    close(fd);
    return NULL;
  }

  lightning_connection_t *conn =
      family == AF_INET
          ? tcp_connect_handshake(fd, max_tokens, error)
          : unix_connect_handshake(fd, max_tokens, shm_size, error);
  if (conn == NULL) {
    close(fd);
  }
  return conn;
}

lightning_listener_t *lightning_bind(const char *address,
                                      lightning_error_t *error) {
  set_error(error, LIGHTNING_OK);

  raw_address_t addr;
  int family;
  socklen_t len;
  if (parse_address(address, &addr, &family, &len) != 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "lightning_bind: invalid address '%s'",
                   address);
    set_error(error, LIGHTNING_ERR_INVALID);
    return NULL;
  }

  int fd = socket(family, SOCK_STREAM, 0);
  if (fd < 0) {
    lightning_log(LIGHTNING_LOG_ERROR, "lightning_bind: socket() failed: %s",
                   strerror(errno));
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }

  if (family == AF_INET) {
    int reuse = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
  } else {
    /* Clear a stale socket file left behind by a previous run, if it
     * exists */
    unlink(addr.un.sun_path);
  }

  if (bind(fd, (struct sockaddr *)&addr, len) != 0 ||
      listen(fd, SOMAXCONN) != 0) {
    int err = errno;
    lightning_log(LIGHTNING_LOG_ERROR,
                   "lightning_bind: failed to bind '%s': %s", address,
                   strerror(err));
    set_error(error, LIGHTNING_ERR_INTERNAL);
    close(fd);
    return NULL;
  }

  struct lightning_listener_t *listener = malloc(sizeof(*listener));
  if (listener == NULL) {
    set_error(error, LIGHTNING_ERR_INTERNAL);
    close(fd);
    return NULL;
  }
  listener->fd = fd;
  listener->family = family;
  return listener;
}

lightning_connection_t *lightning_accept(lightning_listener_t *listener,
                                          lightning_error_t *error) {
  set_error(error, LIGHTNING_OK);

  int fd = accept(listener->fd, NULL, NULL);
  if (fd < 0) {
    lightning_log(LIGHTNING_LOG_ERROR,
                   "lightning_accept: accept() failed: %s", strerror(errno));
    set_error(error, LIGHTNING_ERR_INTERNAL);
    return NULL;
  }
  if (listener->family == AF_INET) {
    tcp_set_congestion_control(fd);
  }

  lightning_connection_t *conn = listener->family == AF_INET
                                      ? tcp_accept_handshake(fd, error)
                                      : unix_accept_handshake(fd, error);
  if (conn == NULL) {
    close(fd);
  }
  return conn;
}

int lightning_shared_memory(const lightning_connection_t *conn, int *fd,
                             size_t *size) {
  if (conn->family != AF_UNIX) {
    return -1;
  }
  return unix_shared_memory(conn, fd, size);
}

lightning_error_t lightning_send(lightning_connection_t *conn,
                                  const lightning_message_t *msg) {
  return conn->family == AF_INET ? tcp_send(conn, msg) : unix_send(conn, msg);
}

lightning_error_t lightning_recv(lightning_connection_t *conn,
                                  lightning_message_t *msg) {
  return conn->family == AF_INET ? tcp_recv(conn, msg) : unix_recv(conn, msg);
}

void lightning_message_free(lightning_message_t *msg) {
  free((void *)msg->source_name);
  free(msg->meta);
  free(msg->data);
  msg->source_name = NULL;
  msg->meta = NULL;
  msg->meta_size = 0;
  msg->data = NULL;
  msg->data_size = 0;
}
