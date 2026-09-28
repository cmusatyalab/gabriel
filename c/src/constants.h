/* Gabriel protocol constants. */

#ifndef GABRIEL_CONSTANTS_H
#define GABRIEL_CONSTANTS_H

/* Address prefixes. */
#define GABRIEL_TCP_PREFIX "tcp://"
#define GABRIEL_UNIX_PREFIX "unix://"
#define GABRIEL_SHM_PREFIX "shm://"

/* Handshake and connection constants. */
#define GABRIEL_BACKOFF_STEP_S 2u       /* size of each backoff step */
#define GABRIEL_BACKOFF_MAX_S 32u       /* maximum backoff time for reconnect */

/* Shared memory chunk constants. */
#define GABRIEL_DEFAULT_CHUNK_COUNT 5u

#endif
