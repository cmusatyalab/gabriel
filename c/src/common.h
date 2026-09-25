/* Helper functions and structures common to multiple files. */

#ifndef LIGHTNING_COMMON_H
#define LIGHTNING_COMMON_H

#include <stdbool.h>
#include <stdint.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/un.h>

/* Holds the data for a consumer address. */
typedef struct lightning_address_t {
    char *address; /* plaintext address */
    int family;    /* AF_INET or AF_UNIX */
    bool shm;      /* uses shared memory */
    socklen_t len; /* socket length */
    union {        
        struct sockaddr_in inet;
        struct sockaddr_un unet;
    } sockaddr;    /* underlying socket address */
} lightning_address_t;

#endif
