#include "gabriel_core.h"

#include <cstdlib>
#include <cstring>

#include <gst/gst.h>

#include "greeting_generated.h"

void gabriel_core_init(void) {
  gst_init(nullptr, nullptr);
}

int gabriel_build_greeting(int32_t id, const char *message,
                            uint8_t **out_buf, size_t *out_len) {
  if (out_buf == nullptr || out_len == nullptr) {
    return 0;
  }

  flatbuffers::FlatBufferBuilder builder;
  auto greeting = fbs::CreateGreetingDirect(builder, id, message);
  builder.Finish(greeting);

  uint8_t *buf = static_cast<uint8_t *>(std::malloc(builder.GetSize()));
  if (buf == nullptr) {
    return 0;
  }
  std::memcpy(buf, builder.GetBufferPointer(), builder.GetSize());

  *out_buf = buf;
  *out_len = builder.GetSize();
  return 1;
}

void gabriel_free_buffer(uint8_t *buf) {
  std::free(buf);
}
