#include "utils.h"

#include <stdint.h>
#include <sys/socket.h>
#include <sys/types.h>

int send_all(int fd, const void *buf, size_t len) {
  const uint8_t *p = buf;
  size_t sent = 0;
  while (sent < len) {
    /* MSG_NOSIGNAL: a peer that already closed its end must surface
     * as a normal -1/EPIPE return, not kill this process with
     * SIGPIPE. */
    ssize_t n = send(fd, p + sent, len - sent, MSG_NOSIGNAL);
    if (n <= 0) {
      return -1;
    }
    sent += (size_t)n;
  }
  return 0;
}

int recv_all(int fd, void *buf, size_t len) {
  uint8_t *p = buf;
  size_t received = 0;
  while (received < len) {
    ssize_t n = recv(fd, p + received, len - received, 0);
    if (n <= 0) {
      return -1;
    }
    received += (size_t)n;
  }
  return 0;
}
