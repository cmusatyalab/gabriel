#include "common.h"

#include <endian.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
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

/* Wire layout: source(GABRIEL_MAX_NAME) token(1) seq_num(8) offset(8)
 * metadata_size(8) data_size(8), all big-endian for the multi-byte
 * fields, followed by metadata_size bytes of metadata (if any) and
 * data_size bytes of data (if any). */
gabriel_error_t send_message(int fd, gabriel_message_t *message) {
    uint8_t token = (uint8_t)message->token;
    uint64_t seq_num_be = htobe64(message->seq_num);
    uint64_t offset_be = htobe64(message->offset);
    uint64_t metadata_size_be = htobe64(message->metadata_size);
    uint64_t data_size_be = htobe64(message->data_size);

    if (!send_all(fd, message->source, GABRIEL_MAX_NAME) ||
            !send_all(fd, &token, sizeof(token)) ||
            !send_all(fd, &seq_num_be, sizeof(seq_num_be)) ||
            !send_all(fd, &offset_be, sizeof(offset_be)) ||
            !send_all(fd, &metadata_size_be, sizeof(metadata_size_be)) ||
            !send_all(fd, &data_size_be, sizeof(data_size_be))) {
        return GABRIEL_ERR_BROKEN_PIPE;
    }
    if (message->metadata_size > 0 &&
            !send_all(fd, message->metadata, message->metadata_size)) {
        return GABRIEL_ERR_BROKEN_PIPE;
    }
    if (message->data_size > 0 && !send_all(fd, message->data, message->data_size)) {
        return GABRIEL_ERR_BROKEN_PIPE;
    }
    return GABRIEL_OK;
}

gabriel_message_t *recv_message(int fd, gabriel_error_t *error) {
    gabriel_message_t *message = calloc(1, sizeof(*message));
    if (message == NULL) {
        set_error(error, GABRIEL_ERR_INTERNAL);
        return NULL;
    }

    uint8_t token = 0;
    uint64_t seq_num_be = 0, offset_be = 0, metadata_size_be = 0, data_size_be = 0;
    if (!recv_all(fd, message->source, GABRIEL_MAX_NAME) ||
            !recv_all(fd, &token, sizeof(token)) ||
            !recv_all(fd, &seq_num_be, sizeof(seq_num_be)) ||
            !recv_all(fd, &offset_be, sizeof(offset_be)) ||
            !recv_all(fd, &metadata_size_be, sizeof(metadata_size_be)) ||
            !recv_all(fd, &data_size_be, sizeof(data_size_be))) {
        goto broken;
    }
    message->source[GABRIEL_MAX_NAME - 1] = '\0'; /* defensive: always terminated */
    message->token = (gabriel_token_t)token;
    message->seq_num = be64toh(seq_num_be);
    message->offset = be64toh(offset_be);
    message->metadata_size = be64toh(metadata_size_be);
    message->data_size = be64toh(data_size_be);

    if (message->metadata_size > 0) {
        uint8_t *metadata = malloc(message->metadata_size);
        if (metadata == NULL || !recv_all(fd, metadata, message->metadata_size)) {
            free(metadata);
            goto broken;
        }
        message->metadata = metadata;
    }
    if (message->data_size > 0) {
        uint8_t *data = malloc(message->data_size);
        if (data == NULL || !recv_all(fd, data, message->data_size)) {
            free(data);
            goto broken;
        }
        message->data = data;
    }

    set_error(error, GABRIEL_OK);
    return message;

broken:
    free((void *)message->metadata);
    free((void *)message->data);
    free(message);
    set_error(error, GABRIEL_ERR_BROKEN_PIPE);
    return NULL;
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
