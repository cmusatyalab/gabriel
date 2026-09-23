#ifndef GABRIEL_INTERNAL_UTILS_H
#define GABRIEL_INTERNAL_UTILS_H

#include <stddef.h>

/* send()/recv() helpers to loop over short writes/reads. Both return 0
 * on full success, or -1 on error/disconnection. */
int send_all(int fd, const void *buf, size_t len);
int recv_all(int fd, void *buf, size_t len);

#endif /* GABRIEL_INTERNAL_UTILS_H */
