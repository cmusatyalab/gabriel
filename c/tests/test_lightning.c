/* Tests for the Lightning C library. Each test runs under a watchdog so
 * a hang fails loudly instead of blocking forever. Set LIGHTNING_LOG=1
 * to print Lightning's internal log. Pass test names as arguments to
 * run a subset. */

#include <lightning/lightning.h>

#include <dirent.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

/* ---- Harness ---- */

static const char *g_test_name;
static bool g_failed;
static char g_dir[64];
static int g_next_port;
static int g_next_path;

#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      fprintf(stderr, "    FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      g_failed = true;                                                  \
      return;                                                           \
    }                                                                   \
  } while (0)

#define CHECK_EQ(a, b)                                                   \
  do {                                                                   \
    long long _a = (long long)(a), _b = (long long)(b);                  \
    if (_a != _b) {                                                      \
      fprintf(stderr, "    FAIL %s:%d: %s == %s (%lld vs %lld)\n",       \
              __FILE__, __LINE__, #a, #b, _a, _b);                       \
      g_failed = true;                                                   \
      return;                                                            \
    }                                                                    \
  } while (0)

static void on_alarm(int sig) {
  (void)sig;
  static const char msg[] = "    FAIL: timed out\n";
  ssize_t n = write(2, msg, sizeof(msg) - 1);
  (void)n;
  _exit(1);
}

static void log_to_stderr(lightning_log_level_t level, const char *message,
                          void *user_data) {
  (void)user_data;
  static const char *const names[] = {"DEBUG", "INFO", "WARN", "ERROR"};
  fprintf(stderr, "      [%s] %s\n", names[level], message);
}

static void sleep_ms(unsigned ms) {
  struct timespec ts = {.tv_sec = ms / 1000, .tv_nsec = (ms % 1000) * 1000000L};
  nanosleep(&ts, NULL);
}

static double now_s(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

/* Fresh address for `scheme` ("unix", "shm" or "tcp"). */
static void make_addr(char *buf, size_t len, const char *scheme) {
  if (strcmp(scheme, "tcp") == 0) {
    snprintf(buf, len, "tcp://127.0.0.1:%d", g_next_port++);
  } else {
    snprintf(buf, len, "%s://%s/s%d", scheme, g_dir, g_next_path++);
  }
}

/* The same socket path under another scheme ("unix" <-> "shm"). */
static void rescheme(char *buf, size_t len, const char *addr,
                     const char *scheme) {
  snprintf(buf, len, "%s://%s", scheme, strstr(addr, "://") + 3);
}

static void fill(uint8_t *buf, size_t len, uint32_t seq) {
  for (size_t i = 0; i < len; i++) {
    buf[i] = (uint8_t)(seq * 31u + i * 7u);
  }
}

static bool check_fill(const uint8_t *buf, size_t len, uint32_t seq) {
  for (size_t i = 0; i < len; i++) {
    if (buf[i] != (uint8_t)(seq * 31u + i * 7u)) {
      return false;
    }
  }
  return true;
}

static lightning_error_t send_seq(lightning_producer_t *p, uint32_t seq,
                                  size_t len) {
  uint8_t buf[4096];
  fill(buf, len, seq);
  return lightning_send(p, buf, len, seq);
}

/* Receives a frame and checks it's `seq` with the fill pattern. */
static bool recv_expect(lightning_consumer_t *c, uint32_t seq, size_t len) {
  lightning_error_t err;
  lightning_message_t *m = lightning_recv(c, &err);
  if (m == NULL) {
    fprintf(stderr, "    recv failed: %d\n", err);
    return false;
  }
  bool ok = err == LIGHTNING_OK && m->seq_num == seq && m->data_size == len &&
            m->token == LIGHTNING_TOKEN_NONE && check_fill(m->data, len, seq);
  if (!ok) {
    fprintf(stderr, "    got seq %u size %llu, want seq %u size %zu\n",
            m->seq_num, (unsigned long long)m->data_size, seq, len);
  }
  lightning_message_free(m);
  return ok;
}

/* Receives a reply and checks it's for `seq` from `name`. */
static bool reply_expect(lightning_producer_t *p, uint32_t seq,
                         const char *name) {
  lightning_error_t err;
  lightning_message_t *m = lightning_recv_reply(p, &err);
  if (m == NULL) {
    fprintf(stderr, "    recv_reply failed: %d\n", err);
    return false;
  }
  bool ok = m->seq_num == seq && m->token == LIGHTNING_TOKEN_ACCEPT &&
            (name == NULL || strcmp(m->source_name, name) == 0);
  if (!ok) {
    fprintf(stderr, "    got reply seq %u from '%s', want seq %u\n",
            m->seq_num, m->source_name, seq);
  }
  lightning_message_free(m);
  return ok;
}

/* Background lightning_recv(), for when the caller needs to keep doing
 * something while waiting for a frame. */
typedef struct {
  lightning_consumer_t *c;
  pthread_t thread;
  atomic_bool done;
  lightning_message_t *msg;
  lightning_error_t err;
} async_recv_t;

static void *async_recv_main(void *arg) {
  async_recv_t *a = arg;
  a->msg = lightning_recv(a->c, &a->err);
  atomic_store(&a->done, true);
  return NULL;
}

static void async_recv_start(async_recv_t *a, lightning_consumer_t *c) {
  memset(a, 0, sizeof(*a));
  a->c = c;
  pthread_create(&a->thread, NULL, async_recv_main, a);
}

/* ---- Tests ---- */

static void roundtrip(const char *scheme) {
  char addr[128];
  make_addr(addr, sizeof(addr), scheme);
  lightning_error_t err;
  lightning_consumer_t *c =
      lightning_create_consumer("cons", "cons-host", 1024, addr, 0, &err);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  lightning_producer_t *p = lightning_create_producer(
      2, 4096, 0, "prod", "prod-host", targets, &err);
  CHECK(p != NULL);
  CHECK_EQ(err, LIGHTNING_OK);

  /* Enough rounds to reuse every chunk and reply chunk many times. */
  for (uint32_t seq = 1; seq <= 50; seq++) {
    size_t len = (seq * 97) % 4096;
    CHECK_EQ(send_seq(p, seq, len), LIGHTNING_OK);

    lightning_message_t *m = lightning_recv(c, &err);
    CHECK(m != NULL);
    CHECK_EQ(m->seq_num, seq);
    CHECK_EQ(m->data_size, len);
    CHECK(check_fill(m->data, len, seq));
    CHECK_EQ(m->token, LIGHTNING_TOKEN_NONE);
    CHECK(strcmp(m->source_name, "prod") == 0);
    CHECK(strcmp(m->source_host, "prod-host") == 0);
    CHECK(strcmp(m->source_ip,
                 strcmp(scheme, "tcp") == 0 ? "127.0.0.1" : "") == 0);
    CHECK(m->source_id != 0);
    lightning_message_free(m);

    char reply[32];
    int n = snprintf(reply, sizeof(reply), "ack %u", seq);
    CHECK_EQ(lightning_reply(c, (uint8_t *)reply, (uint64_t)n, seq),
             LIGHTNING_OK);

    lightning_message_t *r = lightning_recv_reply(p, &err);
    CHECK(r != NULL);
    CHECK_EQ(r->seq_num, seq);
    CHECK_EQ(r->token, LIGHTNING_TOKEN_ACCEPT);
    CHECK_EQ(r->data_size, n);
    CHECK(memcmp(r->data, reply, (size_t)n) == 0);
    CHECK(strcmp(r->source_name, "cons") == 0);
    CHECK(strcmp(r->source_host, "cons-host") == 0);
    lightning_message_free(r);
  }
  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

static void test_roundtrip_unix(void) { roundtrip("unix"); }
static void test_roundtrip_tcp(void) { roundtrip("tcp"); }
static void test_roundtrip_shm(void) { roundtrip("shm"); }

static void test_empty_frames(void) {
  const char *schemes[] = {"unix", "shm"};
  for (int i = 0; i < 2; i++) {
    char addr[128];
    make_addr(addr, sizeof(addr), schemes[i]);
    lightning_consumer_t *c =
        lightning_create_consumer("c", NULL, 16, addr, 0, NULL);
    CHECK(c != NULL);
    const char *targets[] = {addr, NULL};
    lightning_producer_t *p =
        lightning_create_producer(1, 16, 0, "p", NULL, targets, NULL);
    CHECK(p != NULL);
    CHECK_EQ(lightning_send(p, NULL, 0, 1), LIGHTNING_OK);
    lightning_message_t *m = lightning_recv(c, NULL);
    CHECK(m != NULL);
    CHECK_EQ(m->data_size, 0);
    CHECK(strcmp(m->source_host, "") == 0);
    lightning_message_free(m);
    CHECK_EQ(lightning_reply(c, NULL, 0, 1), LIGHTNING_OK);
    CHECK(reply_expect(p, 1, "c"));
    lightning_destroy_producer(p);
    lightning_destroy_consumer(c);
  }
}

static void test_buffered_tokens(void) {
  char addr[128];
  make_addr(addr, sizeof(addr), "unix");
  lightning_consumer_t *c = lightning_create_consumer("c", NULL, 64, addr, 0, NULL);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  lightning_producer_t *p =
      lightning_create_producer(2, 64, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  CHECK_EQ(send_seq(p, 1, 8), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 2, 8), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 3, 8), LIGHTNING_ERR_DROPPED); /* bucket empty */

  CHECK(recv_expect(c, 1, 8));
  CHECK_EQ(lightning_reply(c, NULL, 0, 1), LIGHTNING_OK);
  CHECK(reply_expect(p, 1, "c")); /* its token is back once we have it */
  CHECK_EQ(send_seq(p, 4, 8), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 5, 8), LIGHTNING_ERR_DROPPED);

  /* A repeated token for an already-counted frame isn't counted twice. */
  CHECK(recv_expect(c, 2, 8));
  CHECK_EQ(lightning_reply(c, NULL, 0, 2), LIGHTNING_OK);
  CHECK_EQ(lightning_reply(c, NULL, 0, 2), LIGHTNING_OK);
  CHECK(reply_expect(p, 2, "c"));
  CHECK(reply_expect(p, 2, "c"));
  CHECK_EQ(send_seq(p, 6, 8), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 7, 8), LIGHTNING_ERR_DROPPED);

  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

static void test_per_consumer_tokens(void) {
  char a_addr[128], b_addr[128];
  make_addr(a_addr, sizeof(a_addr), "unix");
  make_addr(b_addr, sizeof(b_addr), "tcp");
  lightning_consumer_t *a = lightning_create_consumer("a", NULL, 64, a_addr, 0, NULL);
  lightning_consumer_t *b = lightning_create_consumer("b", NULL, 64, b_addr, 0, NULL);
  CHECK(a != NULL && b != NULL);
  const char *targets[] = {a_addr, b_addr, NULL};
  lightning_producer_t *p =
      lightning_create_producer(1, 64, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  CHECK_EQ(send_seq(p, 1, 8), LIGHTNING_OK); /* both get 1 */
  CHECK(recv_expect(a, 1, 8));
  CHECK_EQ(lightning_reply(a, NULL, 0, 1), LIGHTNING_OK);
  CHECK(reply_expect(p, 1, "a"));

  /* b is slow (hasn't replied): it's skipped, but a keeps going. */
  for (uint32_t seq = 2; seq <= 5; seq++) {
    CHECK_EQ(send_seq(p, seq, 8), LIGHTNING_OK);
    CHECK(recv_expect(a, seq, 8));
    CHECK_EQ(lightning_reply(a, NULL, 0, seq), LIGHTNING_OK);
    CHECK(reply_expect(p, seq, "a"));
  }

  /* b only ever got frame 1; once it replies, it gets the next send. */
  CHECK(recv_expect(b, 1, 8));
  CHECK_EQ(lightning_reply(b, NULL, 0, 1), LIGHTNING_OK);
  CHECK(reply_expect(p, 1, "b"));
  CHECK_EQ(send_seq(p, 6, 8), LIGHTNING_OK);
  CHECK(recv_expect(b, 6, 8));
  CHECK(recv_expect(a, 6, 8));

  lightning_destroy_producer(p);
  lightning_destroy_consumer(a);
  lightning_destroy_consumer(b);
}

static void test_shm_newest_frame(void) {
  char addr[128];
  make_addr(addr, sizeof(addr), "shm");
  lightning_consumer_t *c = lightning_create_consumer("c", NULL, 64, addr, 0, NULL);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  /* One token, so the pool has exactly one chunk. */
  lightning_producer_t *p =
      lightning_create_producer(1, 64, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  CHECK_EQ(send_seq(p, 1, 16), LIGHTNING_OK); /* handed to c */
  /* c holds the only chunk until it reads frame 1, so this is dropped. */
  CHECK_EQ(send_seq(p, 2, 16), LIGHTNING_ERR_DROPPED);
  CHECK(recv_expect(c, 1, 16));

  /* While c works on frame 1 (no token), frames overwrite each other. */
  CHECK_EQ(send_seq(p, 3, 16), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 4, 16), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 5, 16), LIGHTNING_OK);

  /* Replying returns the token, and c is handed the newest frame. */
  CHECK_EQ(lightning_reply(c, NULL, 0, 1), LIGHTNING_OK);
  CHECK(recv_expect(c, 5, 16));
  CHECK(reply_expect(p, 1, "c"));

  /* Nothing newer than 5: c waits and gets the next send directly. */
  CHECK_EQ(lightning_reply(c, NULL, 0, 5), LIGHTNING_OK);
  CHECK(reply_expect(p, 5, "c"));
  CHECK_EQ(send_seq(p, 6, 16), LIGHTNING_OK);
  CHECK(recv_expect(c, 6, 16));

  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

static void test_shm_newest_of_many(void) {
  char addr[128];
  make_addr(addr, sizeof(addr), "shm");
  lightning_consumer_t *c = lightning_create_consumer("c", NULL, 64, addr, 0, NULL);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  lightning_producer_t *p =
      lightning_create_producer(3, 64, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  /* c uses up its 3 tokens, and reads (releases) all 3 chunks. */
  for (uint32_t seq = 1; seq <= 3; seq++) {
    CHECK_EQ(send_seq(p, seq, 16), LIGHTNING_OK);
  }
  for (uint32_t seq = 1; seq <= 3; seq++) {
    CHECK(recv_expect(c, seq, 16));
  }
  /* The pool now holds 3 frames c hasn't been handed. Each reply must
   * hand it the newest one it hasn't seen, never an older one. */
  for (uint32_t seq = 4; seq <= 6; seq++) {
    CHECK_EQ(send_seq(p, seq, 16), LIGHTNING_OK);
  }
  CHECK_EQ(lightning_reply(c, NULL, 0, 1), LIGHTNING_OK);
  CHECK(recv_expect(c, 6, 16));
  /* Nothing newer than 6 exists, so these just refill the bucket. */
  CHECK_EQ(lightning_reply(c, NULL, 0, 2), LIGHTNING_OK);
  CHECK_EQ(lightning_reply(c, NULL, 0, 3), LIGHTNING_OK);
  CHECK_EQ(lightning_reply(c, NULL, 0, 6), LIGHTNING_OK);
  for (uint32_t seq = 1; seq <= 3; seq++) {
    CHECK(reply_expect(p, seq, "c"));
  }
  CHECK(reply_expect(p, 6, "c"));
  /* Full bucket again: the next 3 sends are each handed right away. */
  for (uint32_t seq = 7; seq <= 9; seq++) {
    CHECK_EQ(send_seq(p, seq, 16), LIGHTNING_OK);
  }
  for (uint32_t seq = 7; seq <= 9; seq++) {
    CHECK(recv_expect(c, seq, 16));
  }

  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

/* Blocks allocated to the Lightning memfd of exactly `size` bytes
 * (found through /proc/self/fd), or -1 if there is none. */
static long long memfd_blocks(off_t size) {
  long long blocks = -1;
  DIR *dir = opendir("/proc/self/fd");
  if (dir == NULL) {
    return -1;
  }
  struct dirent *e;
  while ((e = readdir(dir)) != NULL) {
    char path[300], link[256];
    snprintf(path, sizeof(path), "/proc/self/fd/%s", e->d_name);
    ssize_t n = readlink(path, link, sizeof(link) - 1);
    if (n <= 0) {
      continue;
    }
    link[n] = '\0';
    struct stat st;
    if (strstr(link, "memfd:lightning") != NULL && stat(path, &st) == 0 &&
        st.st_size == size) {
      blocks = (long long)st.st_blocks;
    }
  }
  closedir(dir);
  return blocks;
}

static void test_shm_pool_memory_released(void) {
  char addr[128];
  make_addr(addr, sizeof(addr), "shm");
  lightning_consumer_t *c = lightning_create_consumer("c", NULL, 64, addr, 0, NULL);
  CHECK(c != NULL);
  /* A distinctive pool size: 3 chunks of (64-byte header + 256 KiB). */
  const uint64_t max_send = 256 * 1024;
  const off_t pool_size = 3 * (64 + (off_t)max_send);
  lightning_producer_t *p =
      lightning_create_producer(3, max_send, 0, "p", NULL, NULL, NULL);
  CHECK(p != NULL);
  CHECK_EQ(memfd_blocks(pool_size), 0); /* reserved, not backed */

  CHECK_EQ(lightning_add_target(p, addr), LIGHTNING_OK);
  static uint8_t frame[256 * 1024];
  for (uint32_t seq = 1; seq <= 3; seq++) {
    fill(frame, sizeof(frame), seq);
    CHECK_EQ(lightning_send(p, frame, sizeof(frame), seq), LIGHTNING_OK);
  }
  CHECK(memfd_blocks(pool_size) > 0);

  CHECK_EQ(lightning_remove_target(p, addr), LIGHTNING_OK);
  CHECK_EQ(memfd_blocks(pool_size), 0); /* handed back to the kernel */

  /* And the pool still works after being released. */
  CHECK_EQ(lightning_add_target(p, addr), LIGHTNING_OK);
  fill(frame, sizeof(frame), 10);
  CHECK_EQ(lightning_send(p, frame, sizeof(frame), 10), LIGHTNING_OK);
  lightning_message_t *m = NULL;
  /* Frames 1..3 went to the removed connection, which c discards. */
  m = lightning_recv(c, NULL);
  CHECK(m != NULL);
  CHECK_EQ(m->seq_num, 10);
  CHECK(check_fill(m->data, sizeof(frame), 10));
  lightning_message_free(m);

  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

static void test_staleness(void) {
  char addr[128];
  make_addr(addr, sizeof(addr), "unix");
  lightning_consumer_t *c = lightning_create_consumer("c", NULL, 64, addr, 0, NULL);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  lightning_producer_t *p =
      lightning_create_producer(5, 64, 2, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  for (uint32_t seq = 1; seq <= 5; seq++) {
    CHECK_EQ(send_seq(p, seq, 8), LIGHTNING_OK);
  }
  /* 1..3 are at least 2 behind 5, so they're dropped. */
  CHECK(recv_expect(c, 4, 8));
  CHECK_EQ(lightning_reply(c, NULL, 0, 4), LIGHTNING_OK);
  CHECK(recv_expect(c, 5, 8));
  CHECK_EQ(lightning_reply(c, NULL, 0, 5), LIGHTNING_OK);
  CHECK(reply_expect(p, 4, "c"));
  CHECK(reply_expect(p, 5, "c"));

  /* The dropped frames' tokens came back too: the bucket is full. */
  for (uint32_t seq = 6; seq <= 10; seq++) {
    CHECK_EQ(send_seq(p, seq, 8), LIGHTNING_OK);
  }
  CHECK_EQ(send_seq(p, 11, 8), LIGHTNING_ERR_DROPPED);

  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

static void test_errors(void) {
  lightning_error_t err;
  CHECK(lightning_create_consumer("c", NULL, 64, "foo://bar", 0, &err) == NULL);
  CHECK_EQ(err, LIGHTNING_ERR_INVALID);
  CHECK(lightning_create_consumer("c", NULL, 64, "tcp://nope:1", 0, &err) ==
        NULL);
  CHECK_EQ(err, LIGHTNING_ERR_INVALID);
  const char *bad[] = {"tcp://127.0.0.1", NULL};
  CHECK(lightning_create_producer(1, 64, 0, "p", NULL, bad, &err) == NULL);
  CHECK_EQ(err, LIGHTNING_ERR_INVALID);
  CHECK(lightning_create_producer(0, 64, 0, "p", NULL, NULL, &err) == NULL);
  CHECK_EQ(err, LIGHTNING_ERR_INVALID);

  char addr[128];
  make_addr(addr, sizeof(addr), "unix");
  lightning_consumer_t *c = lightning_create_consumer("c", NULL, 8, addr, 0, NULL);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  lightning_producer_t *p =
      lightning_create_producer(1, 64, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  CHECK_EQ(lightning_reply(c, NULL, 0, 1), LIGHTNING_ERR_INVALID);
  CHECK_EQ(send_seq(p, 1, 65), LIGHTNING_ERR_TOO_LARGE);
  CHECK_EQ(send_seq(p, 1, 64), LIGHTNING_OK);
  CHECK(recv_expect(c, 1, 64));
  uint8_t big[9] = {0};
  CHECK_EQ(lightning_reply(c, big, 9, 1), LIGHTNING_ERR_TOO_LARGE);
  CHECK_EQ(lightning_reply(c, big, 8, 1), LIGHTNING_OK);
  CHECK(reply_expect(p, 1, "c"));

  /* The producer goes away: replying to it is a broken pipe. */
  CHECK_EQ(send_seq(p, 2, 8), LIGHTNING_OK);
  CHECK(recv_expect(c, 2, 8));
  lightning_destroy_producer(p);
  sleep_ms(50);
  lightning_error_t rc = lightning_reply(c, NULL, 0, 2);
  CHECK(rc == LIGHTNING_ERR_BROKEN_PIPE || rc == LIGHTNING_OK);
  lightning_destroy_consumer(c);
}

static void test_dynamic_targets(void) {
  char a[128], b[128];
  make_addr(a, sizeof(a), "unix");
  make_addr(b, sizeof(b), "shm");
  lightning_consumer_t *ca = lightning_create_consumer("a", NULL, 64, a, 0, NULL);
  lightning_consumer_t *cb = lightning_create_consumer("b", NULL, 64, b, 0, NULL);
  CHECK(ca != NULL && cb != NULL);

  lightning_producer_t *p =
      lightning_create_producer(2, 64, 0, "p", NULL, NULL, NULL);
  CHECK(p != NULL);
  CHECK_EQ(send_seq(p, 1, 8), LIGHTNING_ERR_DROPPED); /* no targets */

  CHECK_EQ(lightning_add_target(p, a), LIGHTNING_OK);
  CHECK_EQ(lightning_add_target(p, a), LIGHTNING_ERR_INVALID); /* duplicate */
  CHECK_EQ(lightning_add_target(p, "bogus"), LIGHTNING_ERR_INVALID);
  CHECK_EQ(send_seq(p, 2, 8), LIGHTNING_OK);
  CHECK(recv_expect(ca, 2, 8));
  CHECK_EQ(lightning_reply(ca, NULL, 0, 2), LIGHTNING_OK);
  CHECK(reply_expect(p, 2, "a"));

  CHECK_EQ(lightning_add_target(p, b), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 3, 8), LIGHTNING_OK);
  CHECK(recv_expect(ca, 3, 8));
  CHECK(recv_expect(cb, 3, 8));

  CHECK_EQ(lightning_remove_target(p, a), LIGHTNING_OK);
  CHECK_EQ(lightning_remove_target(p, a), LIGHTNING_ERR_INVALID);
  CHECK_EQ(lightning_remove_target(p, "unix:///not/a/target"),
           LIGHTNING_ERR_INVALID);
  CHECK_EQ(lightning_remove_target(p, b), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 4, 8), LIGHTNING_ERR_DROPPED); /* no targets again */

  /* Re-adding the unbuffered target reuses the (released) pool. */
  CHECK_EQ(lightning_add_target(p, b), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 5, 8), LIGHTNING_OK);
  CHECK(recv_expect(cb, 5, 8));
  CHECK_EQ(lightning_reply(cb, NULL, 0, 5), LIGHTNING_OK);
  CHECK(reply_expect(p, 5, "b"));

  /* Re-adding a buffered target works too, on a fresh connection with a
   * full bucket. */
  CHECK_EQ(lightning_add_target(p, a), LIGHTNING_OK);
  CHECK_EQ(send_seq(p, 6, 8), LIGHTNING_OK);
  CHECK(recv_expect(ca, 6, 8));
  CHECK(recv_expect(cb, 6, 8));

  lightning_destroy_producer(p);
  lightning_destroy_consumer(ca);
  lightning_destroy_consumer(cb);
}

static void test_target_limit(void) {
  lightning_producer_t *p =
      lightning_create_producer(1, 64, 0, "p", NULL, NULL, NULL);
  CHECK(p != NULL);
  char addrs[LIGHTNING_MAX_TARGETS + 1][128];
  /* Nobody listens on these; they're added anyway and retried. */
  for (int i = 0; i <= LIGHTNING_MAX_TARGETS; i++) {
    make_addr(addrs[i], sizeof(addrs[i]), i % 2 ? "shm" : "unix");
  }
  for (int i = 0; i < LIGHTNING_MAX_TARGETS; i++) {
    CHECK_EQ(lightning_add_target(p, addrs[i]), LIGHTNING_OK);
  }
  CHECK_EQ(lightning_add_target(p, addrs[LIGHTNING_MAX_TARGETS]),
           LIGHTNING_ERR_FULL);
  CHECK_EQ(lightning_remove_target(p, addrs[3]), LIGHTNING_OK);
  CHECK_EQ(lightning_add_target(p, addrs[LIGHTNING_MAX_TARGETS]),
           LIGHTNING_OK);

  /* A target whose consumer shows up later is connected in the
   * background. */
  lightning_consumer_t *c =
      lightning_create_consumer("late", NULL, 64, addrs[0], 0, NULL);
  CHECK(c != NULL);
  async_recv_t ar;
  async_recv_start(&ar, c);
  double deadline = now_s() + 5;
  for (uint32_t seq = 1; !atomic_load(&ar.done) && now_s() < deadline; seq++) {
    send_seq(p, seq, 8);
    sleep_ms(20);
  }
  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
  pthread_join(ar.thread, NULL);
  CHECK(ar.msg != NULL);
  CHECK(strcmp(ar.msg->source_name, "p") == 0);
  lightning_message_free(ar.msg);
}

static void test_mixed_targets(void) {
  const char *schemes[] = {"tcp", "unix", "shm"};
  const char *names[] = {"tcp-c", "unix-c", "shm-c"};
  char addrs[3][128];
  lightning_consumer_t *cs[3];
  for (int i = 0; i < 3; i++) {
    make_addr(addrs[i], sizeof(addrs[i]), schemes[i]);
    cs[i] = lightning_create_consumer(names[i], NULL, 64, addrs[i], 0, NULL);
    CHECK(cs[i] != NULL);
  }
  const char *targets[] = {addrs[0], addrs[1], addrs[2], NULL};
  lightning_producer_t *p =
      lightning_create_producer(2, 1024, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  for (uint32_t seq = 1; seq <= 30; seq++) {
    CHECK_EQ(send_seq(p, seq, 1000), LIGHTNING_OK);
    for (int i = 0; i < 3; i++) {
      CHECK(recv_expect(cs[i], seq, 1000));
      CHECK_EQ(lightning_reply(cs[i], (const uint8_t *)names[i],
                               strlen(names[i]), seq),
               LIGHTNING_OK);
    }
    bool seen[3] = {false, false, false};
    for (int i = 0; i < 3; i++) {
      lightning_message_t *r = lightning_recv_reply(p, NULL);
      CHECK(r != NULL);
      CHECK_EQ(r->seq_num, seq);
      for (int j = 0; j < 3; j++) {
        if (strcmp(r->source_name, names[j]) == 0) {
          CHECK(r->data_size == strlen(names[j]) &&
                memcmp(r->data, names[j], r->data_size) == 0);
          seen[j] = true;
        }
      }
      lightning_message_free(r);
    }
    CHECK(seen[0] && seen[1] && seen[2]);
  }
  lightning_destroy_producer(p);
  for (int i = 0; i < 3; i++) {
    lightning_destroy_consumer(cs[i]);
  }
}

static void test_fan_in(void) {
  /* One Unix socket consumer, one buffered and one unbuffered producer,
   * both with the same name. */
  char unix_addr[128], shm_addr[128];
  make_addr(unix_addr, sizeof(unix_addr), "unix");
  rescheme(shm_addr, sizeof(shm_addr), unix_addr, "shm");
  lightning_consumer_t *c =
      lightning_create_consumer("c", NULL, 64, unix_addr, 0, NULL);
  CHECK(c != NULL);
  const char *t1[] = {unix_addr, NULL};
  const char *t2[] = {shm_addr, NULL};
  lightning_producer_t *p1 =
      lightning_create_producer(10, 64, 0, "same", "one", t1, NULL);
  lightning_producer_t *p2 =
      lightning_create_producer(10, 64, 0, "same", "two", t2, NULL);
  CHECK(p1 != NULL && p2 != NULL);

  for (uint32_t seq = 1; seq <= 10; seq++) {
    CHECK_EQ(send_seq(p1, seq, 8), LIGHTNING_OK);
    CHECK_EQ(send_seq(p2, 100 + seq, 8), LIGHTNING_OK);
  }

  /* Round-robin: sources alternate while both have frames queued, and
   * each reply goes back to the producer of the frame just received. */
  uint64_t prev_id = 0;
  uint32_t next[2] = {1, 101};
  for (int i = 0; i < 20; i++) {
    lightning_message_t *m = lightning_recv(c, NULL);
    CHECK(m != NULL);
    CHECK(m->source_id != prev_id);
    prev_id = m->source_id;
    CHECK(strcmp(m->source_name, "same") == 0);
    int which = strcmp(m->source_host, "one") == 0 ? 0 : 1;
    CHECK_EQ(m->seq_num, next[which]);
    next[which]++;
    CHECK(check_fill(m->data, 8, m->seq_num));
    CHECK_EQ(lightning_reply(c, NULL, 0, m->seq_num), LIGHTNING_OK);
    lightning_message_free(m);
  }
  for (uint32_t seq = 1; seq <= 10; seq++) {
    CHECK(reply_expect(p1, seq, "c"));
    CHECK(reply_expect(p2, 100 + seq, "c"));
  }
  lightning_destroy_producer(p1);
  lightning_destroy_producer(p2);
  lightning_destroy_consumer(c);
}

/* The consumer dies holding the producer's only token (and, for shm://,
 * its only chunk), then restarts at the same address. The producer
 * must reconnect and reclaim both. */
static void reconnect(const char *scheme) {
  char addr[128];
  make_addr(addr, sizeof(addr), scheme);
  lightning_consumer_t *c = lightning_create_consumer("c", NULL, 64, addr, 0, NULL);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  lightning_producer_t *p =
      lightning_create_producer(1, 64, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  CHECK_EQ(send_seq(p, 1, 8), LIGHTNING_OK);
  lightning_destroy_consumer(c); /* never read or replied to frame 1 */
  c = lightning_create_consumer("c2", NULL, 64, addr, 0, NULL);
  CHECK(c != NULL);

  async_recv_t ar;
  async_recv_start(&ar, c);
  double deadline = now_s() + 5;
  uint32_t seq = 2;
  while (!atomic_load(&ar.done) && now_s() < deadline) {
    send_seq(p, seq++, 8);
    sleep_ms(20);
  }
  if (!atomic_load(&ar.done)) {
    lightning_destroy_consumer(c); /* wakes the recv */
    pthread_join(ar.thread, NULL);
    lightning_destroy_producer(p);
    CHECK(!"consumer never received a frame after restarting");
  }
  pthread_join(ar.thread, NULL);
  CHECK(ar.msg != NULL);
  uint32_t got = ar.msg->seq_num;
  CHECK(got >= 2);
  CHECK(check_fill(ar.msg->data, 8, got));
  lightning_message_free(ar.msg);
  CHECK_EQ(lightning_reply(c, NULL, 0, got), LIGHTNING_OK);
  CHECK(reply_expect(p, got, "c2"));

  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

static void test_reconnect_unix(void) { reconnect("unix"); }
static void test_reconnect_tcp(void) { reconnect("tcp"); }
static void test_reconnect_shm(void) { reconnect("shm"); }

typedef struct {
  lightning_consumer_t *c;
  lightning_producer_t *p;
  lightning_error_t err;
  bool returned_null;
} blocked_t;

static void *blocked_recv_main(void *arg) {
  blocked_t *b = arg;
  b->returned_null = lightning_recv(b->c, &b->err) == NULL;
  return NULL;
}

static void *blocked_recv_reply_main(void *arg) {
  blocked_t *b = arg;
  b->returned_null = lightning_recv_reply(b->p, &b->err) == NULL;
  return NULL;
}

static void test_destroy_wakes_blocked_calls(void) {
  char addr[128];
  make_addr(addr, sizeof(addr), "unix");
  blocked_t b;
  memset(&b, 0, sizeof(b));
  b.c = lightning_create_consumer("c", NULL, 64, addr, 0, NULL);
  CHECK(b.c != NULL);
  const char *targets[] = {addr, NULL};
  b.p = lightning_create_producer(1, 64, 0, "p", NULL, targets, NULL);
  CHECK(b.p != NULL);

  pthread_t t1, t2;
  pthread_create(&t1, NULL, blocked_recv_main, &b);
  sleep_ms(50);
  lightning_destroy_consumer(b.c);
  pthread_join(t1, NULL);
  CHECK(b.returned_null);
  CHECK_EQ(b.err, LIGHTNING_ERR_CLOSED);

  b.returned_null = false;
  b.err = LIGHTNING_OK;
  pthread_create(&t2, NULL, blocked_recv_reply_main, &b);
  sleep_ms(50);
  lightning_destroy_producer(b.p);
  pthread_join(t2, NULL);
  CHECK(b.returned_null);
  CHECK_EQ(b.err, LIGHTNING_ERR_CLOSED);
}

static void test_small_reply_area(void) {
  char addr[128];
  make_addr(addr, sizeof(addr), "shm");
  /* One reply chunk: each reply waits for the previous to be copied. */
  lightning_consumer_t *c =
      lightning_create_consumer("c", NULL, 4096, addr, 1, NULL);
  CHECK(c != NULL);
  const char *targets[] = {addr, NULL};
  lightning_producer_t *p =
      lightning_create_producer(4, 64, 0, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  for (uint32_t seq = 1; seq <= 4; seq++) {
    CHECK_EQ(send_seq(p, seq, 8), LIGHTNING_OK);
  }
  uint8_t reply[4096];
  for (uint32_t seq = 1; seq <= 4; seq++) {
    CHECK(recv_expect(c, seq, 8));
    fill(reply, sizeof(reply), seq + 1000);
    CHECK_EQ(lightning_reply(c, reply, sizeof(reply), seq), LIGHTNING_OK);
  }
  for (uint32_t seq = 1; seq <= 4; seq++) {
    lightning_message_t *r = lightning_recv_reply(p, NULL);
    CHECK(r != NULL);
    CHECK_EQ(r->seq_num, seq);
    CHECK_EQ(r->data_size, sizeof(reply));
    CHECK(check_fill(r->data, sizeof(reply), seq + 1000));
    lightning_message_free(r);
  }
  lightning_destroy_producer(p);
  lightning_destroy_consumer(c);
}

/* ---- Stress: concurrent send/recv/reply with integrity checks ---- */

#define STRESS_FRAME 65536
#define STRESS_FRAMES 3000

typedef struct {
  lightning_consumer_t *c;
  pthread_t thread;
  atomic_int received;
  atomic_int bad;
  atomic_int out_of_order;
} stress_consumer_t;

static void *stress_consumer_main(void *arg) {
  stress_consumer_t *s = arg;
  uint32_t last = 0;
  unsigned rng = 12345;
  for (;;) {
    lightning_message_t *m = lightning_recv(s->c, NULL);
    if (m == NULL) {
      return NULL; /* destroyed */
    }
    if (m->data_size != STRESS_FRAME ||
        !check_fill(m->data, STRESS_FRAME, m->seq_num)) {
      atomic_fetch_add(&s->bad, 1);
    }
    if (m->seq_num <= last) {
      atomic_fetch_add(&s->out_of_order, 1);
    }
    last = m->seq_num;
    atomic_fetch_add(&s->received, 1);
    /* Vary how long "processing" takes. */
    rng = rng * 1103515245u + 12345u;
    if ((rng >> 16) % 4 == 0) {
      usleep((rng >> 8) % 500);
    }
    uint8_t reply[64];
    fill(reply, sizeof(reply), m->seq_num);
    lightning_reply(s->c, reply, sizeof(reply), m->seq_num);
    lightning_message_free(m);
  }
}

typedef struct {
  lightning_producer_t *p;
  atomic_int replies;
  atomic_int bad;
} stress_replies_t;

static void *stress_replies_main(void *arg) {
  stress_replies_t *s = arg;
  for (;;) {
    lightning_message_t *r = lightning_recv_reply(s->p, NULL);
    if (r == NULL) {
      return NULL;
    }
    if (r->data_size != 64 || !check_fill(r->data, 64, r->seq_num)) {
      atomic_fetch_add(&s->bad, 1);
    }
    atomic_fetch_add(&s->replies, 1);
    lightning_message_free(r);
  }
}

static void test_stress(void) {
  const char *schemes[] = {"shm", "shm", "unix", "tcp"};
  enum { N = 4 };
  char addrs[N][128];
  stress_consumer_t cs[N];
  const char *targets[N + 1];
  for (int i = 0; i < N; i++) {
    make_addr(addrs[i], sizeof(addrs[i]), schemes[i]);
    memset(&cs[i], 0, sizeof(cs[i]));
    cs[i].c = lightning_create_consumer("c", NULL, 64, addrs[i], 2, NULL);
    CHECK(cs[i].c != NULL);
    targets[i] = addrs[i];
  }
  targets[N] = NULL;
  lightning_producer_t *p =
      lightning_create_producer(3, STRESS_FRAME, 2, "p", NULL, targets, NULL);
  CHECK(p != NULL);

  stress_replies_t sr;
  memset(&sr, 0, sizeof(sr));
  sr.p = p;
  pthread_t reply_thread;
  pthread_create(&reply_thread, NULL, stress_replies_main, &sr);
  for (int i = 0; i < N; i++) {
    pthread_create(&cs[i].thread, NULL, stress_consumer_main, &cs[i]);
  }

  uint8_t *frame = malloc(STRESS_FRAME);
  CHECK(frame != NULL);
  int sent = 0;
  for (uint32_t seq = 1; seq <= STRESS_FRAMES; seq++) {
    fill(frame, STRESS_FRAME, seq);
    if (lightning_send(p, frame, STRESS_FRAME, seq) == LIGHTNING_OK) {
      sent++;
    }
    if (seq % 16 == 0) {
      usleep(100);
    }
  }
  free(frame);
  sleep_ms(200);

  lightning_destroy_producer(p);
  pthread_join(reply_thread, NULL);
  for (int i = 0; i < N; i++) {
    lightning_destroy_consumer(cs[i].c);
    pthread_join(cs[i].thread, NULL);
  }

  int total = 0;
  for (int i = 0; i < N; i++) {
    fprintf(stderr, "      %-4s consumer: %d frames\n", schemes[i],
            atomic_load(&cs[i].received));
    CHECK(atomic_load(&cs[i].received) > 0);
    CHECK_EQ(atomic_load(&cs[i].bad), 0);
    CHECK_EQ(atomic_load(&cs[i].out_of_order), 0);
    total += atomic_load(&cs[i].received);
  }
  fprintf(stderr, "      sent %d, replies %d\n", sent,
          atomic_load(&sr.replies));
  CHECK(sent > 0);
  CHECK_EQ(atomic_load(&sr.bad), 0);
  CHECK(atomic_load(&sr.replies) > 0);
  CHECK(atomic_load(&sr.replies) <= total);
}

/* ---- Main ---- */

typedef struct {
  const char *name;
  void (*fn)(void);
} test_t;

static const test_t kTests[] = {
    {"roundtrip_unix", test_roundtrip_unix},
    {"roundtrip_tcp", test_roundtrip_tcp},
    {"roundtrip_shm", test_roundtrip_shm},
    {"empty_frames", test_empty_frames},
    {"buffered_tokens", test_buffered_tokens},
    {"per_consumer_tokens", test_per_consumer_tokens},
    {"shm_newest_frame", test_shm_newest_frame},
    {"shm_newest_of_many", test_shm_newest_of_many},
    {"shm_pool_memory_released", test_shm_pool_memory_released},
    {"staleness", test_staleness},
    {"errors", test_errors},
    {"dynamic_targets", test_dynamic_targets},
    {"target_limit", test_target_limit},
    {"mixed_targets", test_mixed_targets},
    {"fan_in", test_fan_in},
    {"reconnect_unix", test_reconnect_unix},
    {"reconnect_tcp", test_reconnect_tcp},
    {"reconnect_shm", test_reconnect_shm},
    {"destroy_wakes_blocked_calls", test_destroy_wakes_blocked_calls},
    {"small_reply_area", test_small_reply_area},
    {"stress", test_stress},
};

static bool selected(int argc, char **argv, const char *name) {
  if (argc <= 1) {
    return true;
  }
  for (int i = 1; i < argc; i++) {
    if (strcmp(argv[i], name) == 0) {
      return true;
    }
  }
  return false;
}

int main(int argc, char **argv) {
  signal(SIGPIPE, SIG_IGN); /* Lightning must never rely on this */
  signal(SIGALRM, on_alarm);
  if (getenv("LIGHTNING_LOG") != NULL) {
    lightning_set_log_callback(log_to_stderr, NULL);
  }
  snprintf(g_dir, sizeof(g_dir), "/tmp/lightning-test-XXXXXX");
  if (mkdtemp(g_dir) == NULL) {
    perror("mkdtemp");
    return 1;
  }
  g_next_port = 20000 + (int)(getpid() % 20000);

  int failed = 0, ran = 0;
  for (size_t i = 0; i < sizeof(kTests) / sizeof(kTests[0]); i++) {
    if (!selected(argc, argv, kTests[i].name)) {
      continue;
    }
    g_test_name = kTests[i].name;
    g_failed = false;
    fprintf(stderr, "[ RUN  ] %s\n", g_test_name);
    double start = now_s();
    alarm(30);
    kTests[i].fn();
    alarm(0);
    fprintf(stderr, "[ %s ] %s (%.0f ms)\n", g_failed ? "FAIL" : " OK ",
            g_test_name, (now_s() - start) * 1000);
    failed += g_failed;
    ran++;
  }
  rmdir(g_dir); /* consumers remove their own socket files */
  fprintf(stderr, "%d/%d tests passed\n", ran - failed, ran);
  return failed == 0 ? 0 : 1;
}
