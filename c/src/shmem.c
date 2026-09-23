#define _GNU_SOURCE /* memfd_create() */

#include "shmem.h"

#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <unistd.h>

int create_shared_memory(size_t size) {
  int fd = memfd_create("lightning-shm", MFD_CLOEXEC);
  if (fd < 0) {
    return -1;
  }
  if (ftruncate(fd, (off_t)size) != 0) {
    close(fd);
    return -1;
  }
  return fd;
}

int send_shared_memory(int fd, int shm_fd, uint64_t shm_size) {
  /* iovec struct is needed for MacOS */
  struct iovec iov = {.iov_base = &shm_size, .iov_len = sizeof(shm_size)};
  union {
    char buf[CMSG_SPACE(sizeof(int))];
    struct cmsghdr align;
  } control;

  struct msghdr msg;
  memset(&msg, 0, sizeof(msg));
  msg.msg_iov = &iov;
  msg.msg_iovlen = 1;
  msg.msg_control = control.buf;
  msg.msg_controllen = sizeof(control.buf);

  struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
  cmsg->cmsg_level = SOL_SOCKET;
  cmsg->cmsg_type = SCM_RIGHTS;
  cmsg->cmsg_len = CMSG_LEN(sizeof(int));
  memcpy(CMSG_DATA(cmsg), &shm_fd, sizeof(int));

  return sendmsg(fd, &msg, 0) == (ssize_t)sizeof(shm_size) ? 0 : -1;
}

int recv_shared_memory(int fd, int *out_shm_fd, uint64_t *out_shm_size) {
  uint64_t shm_size = 0;
  struct iovec iov = {.iov_base = &shm_size, .iov_len = sizeof(shm_size)};
  union {
    char buf[CMSG_SPACE(sizeof(int))];
    struct cmsghdr align;
  } control;

  struct msghdr msg;
  memset(&msg, 0, sizeof(msg));
  msg.msg_iov = &iov;
  msg.msg_iovlen = 1;
  msg.msg_control = control.buf;
  msg.msg_controllen = sizeof(control.buf);

  if (recvmsg(fd, &msg, 0) != (ssize_t)sizeof(shm_size)) {
    return -1;
  }

  struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
  if (cmsg == NULL || cmsg->cmsg_type != SCM_RIGHTS) {
    return -1;
  }
  memcpy(out_shm_fd, CMSG_DATA(cmsg), sizeof(int));
  *out_shm_size = shm_size;
  return 0;
}
