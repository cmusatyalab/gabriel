#include "common.h"

#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/uio.h>

bool send_all(int fd, const void *buf, size_t len) {
    const uint8_t *p = buf;
    while (len > 0) {
        ssize_t n = send(fd, p, len, 0);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            return false;
        }
        p += (size_t)n;
        len -= (size_t)n;
    }
    return true;
}

bool recv_all(int fd, void *buf, size_t len) {
    uint8_t *p = buf;
    while (len > 0) {
        ssize_t n = recv(fd, p, len, 0);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            return false;
        }
        p += (size_t)n;
        len -= (size_t)n;
    }
    return true;
}

bool send_fd(int sock, int fd_to_send) {
    uint8_t byte = 0; /* SCM_RIGHTS needs at least one byte of ordinary data */
    struct iovec iov = {.iov_base = &byte, .iov_len = 1};
    union {
        char buf[CMSG_SPACE(sizeof(int))];
        struct cmsghdr align;
    } control;
    memset(&control, 0, sizeof(control));

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
    memcpy(CMSG_DATA(cmsg), &fd_to_send, sizeof(int));

    ssize_t n;
    do {
        n = sendmsg(sock, &msg, 0);
    } while (n < 0 && errno == EINTR);
    return n == 1;
}

void set_error(gabriel_error_t *error, gabriel_error_t value) {
    if (error != NULL) {
        *error = value;
    }
}

/* Dispatches to whatever callback was registered with
 * gabriel_set_log_callback(), if any. */
static gabriel_log_fn g_log_fn = NULL;
static void *g_log_user_data = NULL;

void gabriel_set_log_callback(gabriel_log_fn fn, void *user_cb) {
    g_log_fn = fn;
    g_log_user_data = user_cb;
}

void gabriel_log(gabriel_log_level_t level, const char *fmt, ...) {
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
