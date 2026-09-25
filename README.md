# Lightning

Lightning is the underlying communication mechanism for Gabriel. It is a token-based flow control producer-consumer system. A producer fans frames out to any number of consumers, and each consumer fans frames in from any number of producers and replies to them. Whenever a consumer asks for a frame, it gets the newest one available from each producer.

Every connection runs in one of two modes, chosen by its address prefix. A single producer or consumer can mix both.

- **Buffered** (`tcp://` or `unix://`) sends the data over the socket. Frames are pushed to a consumer as long as it has a token, so several can be in flight at once. This hides network latency: the producer never waits for a reply before sending the next frame.
- **Unbuffered** (`shm://`) uses a Unix Domain Socket for signaling and shared memory for the data. Each frame is written once into the producer's shared memory pool, and a consumer is handed the newest frame whenever it has a token. Nothing queues up, which is ideal locally where a round trip is nearly free.

## Building and testing

```sh
cmake -S . -B build && cmake --build build   # C library (lightning) + tests
(cd build && ctest --output-on-failure)       # or ./build/c/test_lightning [test ...]
go test ./go/                                 # Go bindings (cgo builds the C sources)
pip install . && pytest python/tests          # Python bindings
```

Set `LIGHTNING_LOG=1` when running `test_lightning` to print Lightning's internal log. The C library is Linux-only (it uses `memfd_create` and `eventfd`).

## Scope

The C library (and its C ABI) is Lightning only. It contains no references to Gabriel. The Gabriel client and server logic is written in Go/Python on top of the Lightning ABI.

## Code layout

| File                 | What's in it                                                                 |
| -------------------- | ---------------------------------------------------------------------------- |
| `c/include/lightning/lightning.h` | The public API.                                                 |
| `c/src/internal.h`, `c/src/common.c` | Helpers: logging, addresses, socket I/O, shared memory, the wire protocol, the handshake, messages. |
| `c/src/producer.c`   | The producer: target table, reader and connector threads, the shared memory pool's bookkeeping. |
| `c/src/consumer.c`   | The consumer: accept thread, the receive loop, replies.                      |
| `c/tests/test_lightning.c` | Tests.                                                                  |

## Addresses

| Address           | Mode       | Transport                   |
| ----------------- | ---------- | --------------------------- |
| `tcp://host:port` | buffered   | TCP                         |
| `unix://path`     | buffered   | Unix socket                 |
| `shm://path`      | unbuffered | Unix socket + shared memory |

A producer's targets choose the mode of each connection. A consumer binds either a `tcp://` address or a Unix socket path (`unix://path` and `shm://path` are equivalent when binding). A consumer bound to a Unix socket accepts both buffered and unbuffered producers, and each producer declares its mode in the handshake. A consumer bound to `tcp://` only accepts buffered producers.

## Concepts

### Producers and consumers
A producer connects to a set of consumer addresses (targets) that can change at runtime, at most `LIGHTNING_MAX_TARGETS` (31) at once, and fans each frame out to them. A consumer binds a single address, accepts any number of producers, and takes frames from them in turn (round-robin). A consumer replies to each frame; the reply travels back to the producer that sent it.

### Tokens (per consumer)
A producer keeps a separate token bucket for every consumer, each holding up to `max_tokens` tokens. Handing a frame to a consumer takes one token from its bucket, so a slow consumer only throttles itself.

Every frame a consumer is handed owes the producer exactly one token back:
- A **REPLY** returns it (`lightning_reply`). Each frame takes exactly one reply; a second one is rejected.
- A **DROP** returns it without a reply. The consumer sends one automatically for a frame that was superseded by a newer one before `lightning_recv` returned it, and for the previous frame if `lightning_recv` is called again without replying to it.

So tokens can't leak: every frame is either replied to or dropped. When a consumer disconnects, the producer reclaims its tokens anyway, and the consumer starts with a full bucket when it reconnects.

### Newest frame wins
`lightning_recv` always returns the newest frame from the producer whose turn it is:
- **At the consumer:** it reads every frame that has arrived from that producer, keeps the newest, and DROPs the older ones.
- **At the producer (unbuffered only):** a consumer is only ever handed the newest published frame, and only when it has a token. Frames published while it was busy are skipped.

"Newest" means newest *that has arrived*. Over TCP, a frame still in transit isn't visible yet, so recv returns the newest complete one; guaranteeing the producer's actual newest frame would need a round trip per frame.

### Dispatch
**Buffered consumers (push).** `lightning_send` writes the frame to the socket of every connected buffered consumer that has a token. One without a token is skipped for that frame.

**Unbuffered consumers (pull).** `lightning_send` writes the frame once into a chunk of the shared memory pool and makes it the producer's `latest` frame. A consumer is handed `latest` when:
- `lightning_send` publishes it and the consumer has a token, or
- the consumer returns a token (REPLY or DROP) and hasn't been handed `latest` yet.

A consumer that connects is never handed a frame published before it connected; it gets the next one sent.

### Dynamic targets
Targets can be given at creation and added or removed at any time with `lightning_add_target` / `lightning_remove_target`, from any thread, while `lightning_send` and `lightning_recv_reply` are running.

- **Adding** tries to connect once, synchronously, so a target whose consumer is already up is usable as soon as the call returns. If the consumer isn't reachable yet, the target is still added and is retried in the background (see Reconnection).
- **Removing** closes the connection, reclaims the target's tokens and held chunks, and stops reconnecting. Replies already received from that consumer are still delivered by `lightning_recv_reply`.
- **Disconnect vs remove:** a disconnected target is still a target, and the producer keeps reconnecting to it. Only removal stops that.

### Sequence numbers
Every frame carries the `uint32_t seq_num` given to `lightning_send`, and its reply carries it back, so the caller can match replies to frames. Lightning doesn't interpret it: which frame is newest is tracked internally, in the order frames were sent.

### Identity
Every producer and consumer has an identity made up of:
- `source_name`: the name given at creation. It does not need to be unique.
- `source_id`: a random 64-bit ID generated at creation, which disambiguates endpoints with the same name.
- `source_ip`: the peer's IP address as seen by the other side, if available (empty for Unix socket connections).
- `source_host`: an optional `host_name` given at creation. It is meant for TCP connections over the internet, where the IP alone may be meaningless (NAT, proxies). It is empty if not set.

All four are exchanged during the handshake and exposed on every received `lightning_message_t`.

### Sizes
`max_send_size` always means the maximum data size of messages sent **by the side that sets it**: frames for a producer, replies for a consumer. Each side tells the other its `max_send_size` during the handshake, so the receiving side can reject anything larger. Sending more than `max_send_size` bytes returns `LIGHTNING_ERR_TOO_LARGE`.

### Reconnection
A consumer always restarts at the same address. If a producer loses a connection to a target (broken pipe or a similar error), it drops that consumer and, until the target is removed, keeps retrying the same target address in the background with capped exponential backoff (50 ms doubling up to 1 s). Once it reconnects, a fresh handshake is done and the consumer starts with a full bucket.

### Threading
- **Producer:** `lightning_send`, `lightning_recv_reply`, `lightning_add_target` and `lightning_remove_target` may all be called concurrently from different threads (typically one sending thread and one receiving thread). Concurrent calls to `lightning_send` are serialized. Internally, a **reader thread** handles everything consumers send back, and a **connector thread** retries unreachable targets.
- **Consumer:** `lightning_recv` and `lightning_reply` must be called from the same thread. Internally, an **accept thread** accepts and handshakes new producers; it has to be separate because a connecting producer waits for the consumer's half of the handshake, while your thread may be busy with a frame.
- `lightning_destroy_*` may be called from any thread. Any call blocked on that producer or consumer returns `LIGHTNING_ERR_CLOSED`. After destroy returns, the handle must not be used.

## API

### void lightning_set_log_callback(lightning_log_fn fn, void* user_data)
Registers a callback that receives Lightning's internal diagnostic messages (connections, disconnects, handshake failures), with `user_data` passed through unchanged. Lightning is silent by default, and passing NULL stops logging. The callback may be called from Lightning's background threads. Set it once at startup, before other calls.

### const char* lightning_version(void)
Returns the library version string, for example `"0.2.0"`.

### lightning_producer_t* lightning_create_producer(uint32_t max_tokens, uint64_t max_send_size, const char* source_name, const char* host_name, const char** targets, lightning_error_t* error)
Creates a producer with name `source_name`. `targets` is an optional NULL-terminated list of initial targets (any mix of `tcp://`, `unix://` and `shm://`, at most 31), and each is added as if by `lightning_add_target`. Pass NULL to start with no targets. `max_tokens` is the size of each consumer's token bucket. `host_name` may be NULL.

Creating a producer also creates its shared memory pool (see Unbuffered connections) and starts the reader and connector threads.

### lightning_error_t lightning_add_target(lightning_producer_t* producer, const char* address)
Adds a target (see Dynamic targets). Returns `LIGHTNING_OK` whether or not the consumer was reachable, `LIGHTNING_ERR_INVALID` if the address is malformed or is already a target, and `LIGHTNING_ERR_FULL` if the producer already has 31 targets.

### lightning_error_t lightning_remove_target(lightning_producer_t* producer, const char* address)
Removes a target (see Dynamic targets). `address` must match the string it was added with. Returns `LIGHTNING_ERR_INVALID` if it isn't a target.

### void lightning_destroy_producer(lightning_producer_t* producer)
Stops the background threads, closes all connections, unmaps the pool, and frees the producer.

### lightning_error_t lightning_send(lightning_producer_t* producer, const uint8_t* data, uint64_t data_size, uint32_t seq_num)
Sends a frame (see Dispatch): publishes it to the pool if any unbuffered consumer is connected, and pushes it to every buffered consumer with a token. A failure on one connection drops that consumer (see Reconnection) and does not fail the send for the others.

Returns `LIGHTNING_OK` if the frame was published or pushed to at least one consumer. A published frame counts even if no unbuffered consumer had a token, since they'll be handed it (or something newer) when their tokens return. Returns `LIGHTNING_ERR_DROPPED` otherwise, and `LIGHTNING_ERR_TOO_LARGE` if `data_size > max_send_size`.

### lightning_message_t* lightning_recv_reply(lightning_producer_t* producer, lightning_error_t* error)
Blocks until a reply from any consumer is available, then returns it. The `source_*` fields describe the consumer that sent it, and `seq_num` is the frame the reply answers. By the time a reply is returned, its token is already back in the consumer's bucket. Returns NULL and populates `error` on failure.

### lightning_consumer_t* lightning_create_consumer(const char* source_name, const char* host_name, uint64_t max_send_size, const char* address, lightning_error_t* error)
Creates a consumer with name `source_name`, binds at `address`, and starts the accept thread. `max_send_size` is the maximum size of the replies this consumer sends. `host_name` may be NULL.

### void lightning_destroy_consumer(lightning_consumer_t* consumer)
Stops the accept thread, closes all connections, removes the socket file for a Unix socket address, and frees the consumer.

### lightning_message_t* lightning_recv(lightning_consumer_t* consumer, lightning_error_t* error)
Blocks until a frame is available from any producer, then returns it. Producers take turns (round-robin), and from each you get the newest frame that has arrived (see Newest frame wins). If the previous frame was never replied to, its token is returned (DROP) first. The returned message owns its own copy of the data. Returns NULL and populates `error` if the recv fails for any reason. A producer that disconnects is removed silently.

### lightning_error_t lightning_reply(lightning_consumer_t* consumer, const uint8_t* data, uint64_t data_size)
Replies to the frame most recently returned by `lightning_recv`, which returns its token to the producer. Returns `LIGHTNING_ERR_INVALID` if there is no frame to reply to (nothing received yet, or already replied), `LIGHTNING_ERR_TOO_LARGE` if `data_size > max_send_size` (the frame can still be replied to), and `LIGHTNING_ERR_BROKEN_PIPE` if its producer has disconnected.

### void lightning_message_free(lightning_message_t* msg)
Frees a message returned by `lightning_recv` or `lightning_recv_reply`. Safe to call with NULL.

## Connection internals

### Wire protocol
Every message on a socket starts with a 32-byte big-endian header, `{type, seq, chunk, data_size, payload_size}`, followed by `payload_size` bytes of payload:

| Type    | Direction           | Meaning                                                                 |
| ------- | ------------------- | ----------------------------------------------------------------------- |
| `FRAME` | producer → consumer | A frame. Buffered: the data is the payload. Unbuffered: no payload; `chunk` says where the data is in the pool. |
| `REPLY` | consumer → producer | A reply (always the payload, in both modes), returning the frame's token. `chunk` echoes the frame's. |
| `DROP`  | consumer → producer | Returns a frame's token without a reply. `chunk` echoes the frame's.    |

Sockets are read without blocking, one message at a time, so a producer sending a large frame slowly over TCP never blocks a thread that serves other sockets.

### Handshake
The producer sends a hello: magic, version, mode (buffered or unbuffered), identity, `max_send_size`, and the pool's layout (chunk count and stride). For `shm://` it also passes the pool's memfd over the socket (`SCM_RIGHTS`). The consumer answers with its own hello, with `ack` set to accept (or 0 to reject, for example an unbuffered producer on a TCP consumer). Both sides apply a 2-second timeout to the handshake.

### Unbuffered connections: the shared memory pool
Each producer has one pool, shared by all of its unbuffered consumers. It is a memfd divided into equal chunks, each big enough for one frame. The producer maps it read-write; consumers map it **read-only**.

**Size.** The pool has `max_tokens × 31 + 2` chunks: enough for every consumer to hold `max_tokens` different chunks, plus `latest`, plus one being written, so a send never has to drop for lack of a chunk. This only reserves address space. Pages get memory when written, and because a send always reuses the **lowest-index** free chunk, only the handful of chunks in use at the same time ever get memory (about three for one consumer).

**Bookkeeping.** All of it lives in the producer's own memory under one mutex (`state_mu`), with no atomics in shared memory:
- `refs[i]`: how many consumers hold chunk `i` (plus one while `lightning_send` is writing it).
- `latest`: the chunk with the newest frame. It is never overwritten, even when no one holds it, so there is always a newest frame to hand out.
- `gens[i]`: an internal counter of publishes, used to tell whether a consumer has already been handed the newest frame.
- Per consumer: its tokens, the generation it was last handed, and the list of chunks it holds.

A chunk is **free** when `refs[i] == 0 && i != latest`.

**The four operations:**
1. **Send:** claim the lowest free chunk (`refs = 1`), copy the frame in *without* the lock, then under the lock drop the writer's hold, make it `latest`, and hand it to every unbuffered consumer that has a token.
2. **Hand out** (`dispatch`): if the consumer has a token and hasn't been handed `latest`, write a FRAME with `chunk = latest`, then `refs[latest]++`, add it to the consumer's held list, and take a token.
3. **Token returned** (REPLY or DROP, in the reader thread): remove the echoed chunk from the consumer's held list (a chunk it doesn't hold is a protocol error), `refs--`, give the token back, and hand out `latest` if the consumer hasn't had it.
4. **Consumer gone** (disconnect or removal): release every chunk in its held list.

**Why it's safe without atomics.** A consumer only learns about a chunk from a FRAME message, which is written after the copy has finished and the lock has been released. The socket send and receive are system calls, which order the copy before the consumer's read. After that, the chunk can't be reused until the consumer returns its token, which it only does after it has copied the data out in `lightning_recv`.

## Message Definitions

```c
typedef struct lightning_message_t {
  const char *source_name;  /* sender's source_name */
  uint64_t source_id;       /* sender's random unique ID */
  const char *source_ip;    /* sender's IP, "" if unavailable (Unix socket) */
  const char *source_host;  /* sender's host_name, "" if not set */
  uint32_t seq_num;         /* frame seq_num (or the frame a reply answers) */
  uint64_t data_size;
  uint8_t *data;
} lightning_message_t;
```

## Errors

```c
typedef enum {
  LIGHTNING_OK = 0,
  LIGHTNING_ERR_DROPPED,      /* no consumer could take the frame so it was dropped */
  LIGHTNING_ERR_TOO_LARGE,    /* data_size > max_send_size */
  LIGHTNING_ERR_BROKEN_PIPE,  /* the peer disconnected */
  LIGHTNING_ERR_INVALID,      /* bad address/argument, or no frame to reply to */
  LIGHTNING_ERR_FULL,         /* already at LIGHTNING_MAX_TARGETS targets */
  LIGHTNING_ERR_CLOSED,       /* the handle was destroyed */
  LIGHTNING_ERR_INTERNAL,     /* internal failure catch-all */
} lightning_error_t;
```
