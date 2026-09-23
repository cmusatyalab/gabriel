#ifndef GABRIEL_ERRORS_H
#define GABRIEL_ERRORS_H

#ifdef __cplusplus
extern "C" {
#endif

/* Lightning custom error codes. */
typedef enum {
  LIGHTNING_OK = 0,

  /* lightning_send() dropped the message because no token was
   * available. */
  LIGHTNING_ERR_NO_TOKEN,

  /* The peer disconnected. */
  LIGHTNING_ERR_BROKEN_PIPE,

  /* lightning_recv() found nothing waiting right now - not an error,
   * just "try again later" (a "unix://" connection's recv() never
   * blocks). */
  LIGHTNING_ERR_WOULD_BLOCK,

  /* `address` isn't a valid "tcp://host:port" or "unix://path". */
  LIGHTNING_ERR_INVALID,

  /* Internal failure catch-all. */
  LIGHTNING_ERR_INTERNAL,
} lightning_error_t;

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* GABRIEL_ERRORS_H */
