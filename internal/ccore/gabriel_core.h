#ifndef GABRIEL_CORE_H
#define GABRIEL_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Initializes GStreamer. Must be called once before any pipeline work
 * (there is none yet in this skeleton, but this proves the dependency
 * links and initializes correctly). */
void gabriel_core_init(void);

/* Builds a FlatBuffer-encoded Greeting{id, message} and writes a
 * malloc'd copy of the buffer to *out_buf/*out_len. The caller owns the
 * returned buffer and must release it with gabriel_free_buffer.
 * Returns 1 on success, 0 on failure (e.g. allocation failure or NULL
 * out_buf/out_len). */
int gabriel_build_greeting(int32_t id, const char *message,
                            uint8_t **out_buf, size_t *out_len);

/* Frees a buffer previously returned by gabriel_build_greeting. */
void gabriel_free_buffer(uint8_t *buf);

#ifdef __cplusplus
}
#endif

#endif /* GABRIEL_CORE_H */
