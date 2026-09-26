/* Lightning constants. */

#ifndef LIGHTNING_CONSTANTS_H
#define LIGHTNING_CONSTANTS_H

/* Handshake and connection constants. */
#define LIGHTNING_CONNECT_TIMEOUT_MS 1000u   /* connection timeout */
#define LIGHTNING_HANDSHAKE_TIMEOUT_MS 2000u /* handshake timeout */
#define LIGHTNING_BACKOFF_MIN_MS 50u         /* minimum backoff time for reconnect */
#define LIGHTNING_BACKOFF_MAX_MS 1000u       /* maximum backoff time for reconnect */

#endif
