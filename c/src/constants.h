/* Lightning constants. */

#ifndef LIGHTNING_CONSTANTS_H
#define LIGHTNING_CONSTANTS_H

/* Handshake and connection constants. */
#define CONNECT_TIMEOUT_MS 1000u   /* connection timeout */
#define HANDSHAKE_TIMEOUT_MS 2000u /* handshake timeout */
#define BACKOFF_MIN_MS 50u         /* minimum backoff time for reconnect */
#define BACKOFF_MAX_MS 1000u       /* maximum backoff time for reconnect */

#endif
