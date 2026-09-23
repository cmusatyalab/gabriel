#ifndef GABRIEL_INTERNAL_SHMEM_H
#define GABRIEL_INTERNAL_SHMEM_H

#include <stddef.h>
#include <stdint.h>

/* Creates an anonymous, already-sized shared memory segment. Returns
 * its fd, or -1 on failure. */
int create_shared_memory(size_t size);

/* Sends `shm_fd` and its size to the peer over a Unix domain socket,
 * via SCM_RIGHTS ancillary data. This duplicates `shm_fd` into the
 * peer's process; it doesn't close or otherwise affect the caller's
 * copy. */
int send_shared_memory(int fd, int shm_fd, uint64_t shm_size);

/* Receives a shared memory fd and its size, as sent by
 * send_shared_memory(). The returned fd is owned by the caller. */
int recv_shared_memory(int fd, int *out_shm_fd, uint64_t *out_shm_size);

#endif /* GABRIEL_INTERNAL_SHMEM_H */
