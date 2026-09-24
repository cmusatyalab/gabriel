# Lightning

Lightning is the underlying communication mechanism for Gabriel. It is a token-based flow control producer-consumer system. A producer fans frames out to any number of consumers, and each consumer fans frames in from any number of producers and replies to them.

Every connection runs in one of two modes, chosen by its address prefix. A single producer or consumer can mix both.

- **Buffered** (`tcp://` or `unix://`) sends the data over the socket. Frames are pushed to a consumer as long as it has a token, so several can be in flight at once. This hides network latency: the producer never waits for a reply before sending the next frame.
- **Unbuffered** (`shm://`) uses a Unix Domain Socket for signaling and blazingly fast shared memory for the data. A consumer is handed a frame only when it is ready for one (when it returns a token), and it always gets the newest frame. Nothing queues up, which is ideal locally where a round trip is nearly free.

## Building and testing

```sh
cmake -S . -B build && cmake --build build   # C library (lightning) + tests
(cd build && ctest --output-on-failure)       # or ./build/c/test_lightning [test ...]
go test ./go/                                 # Go bindings (cgo builds the C sources)
pip install . && pytest python/tests          # Python bindings
```

Set `LIGHTNING_LOG=1` when running `test_lightning` to print Lightning's internal log. The C library is Linux-only (it uses `memfd_create`, `eventfd` and `fallocate`).

## Scope

The C library (and its C ABI) is Lightning only. It contains no references to Gabriel. The Gabriel client and server logic is written in Go/Python on top of the Lightning ABI.

## Addresses

| Address           | Mode       | Transport                   |
| ----------------- | ---------- | --------------------------- |
| `tcp://host:port` | buffered   | TCP                         |
| `unix://path`     | buffered   | Unix socket                 |
| `shm://path`      | unbuffered | Unix socket + shared memory |

A producer's targets choose the mode of each connection. A consumer binds either a `tcp://` address or a Unix socket path (`unix://path` and `shm://path` are equivalent when binding). A consumer bound to a Unix socket accepts both buffered and unbuffered producers, and each producer declares its mode in the handshake. A consumer bound to `tcp://` only accepts buffered producers.

Internally, each connection holds a pointer to a small table of operations (handshake, send frame, read message, send reply, teardown) implemented once for buffered and once for unbuffered. The producer and consumer logic above them is shared.

## Concepts

### Producers and consumers
A producer connects to a set of consumer addresses (targets) that can change at runtime, at most 31 at once, and fans each frame out to its consumers. Frames are pushed to buffered consumers and pulled by unbuffered consumers (see Dispatch). A consumer binds a single address, accepts any number of producers in a background thread, and reads from them round-robin (fan-in). A consumer replies to frames; replies travel back to the producer that sent the frame.

### Tokens (per consumer)
A producer keeps a separate token bucket for every consumer, each holding up to `max_tokens` tokens. Handing a frame to a consumer takes one token from its bucket, so a slow consumer only throttles itself.

A consumer returns exactly one token for every frame it receives:
- `TOKEN_ACCEPT`, carried by the reply to that frame (`lightning_reply`).
- `TOKEN_DROP`, sent automatically by `lightning_recv` for each frame it discards as stale.

A returned token refills that consumer's bucket (up to `max_tokens`) only if its `seq_num` is greater than the highest `seq_num` that has returned a token from that consumer. This prevents double counting of tokens for a single frame.

Tokens are always reclaimed by the producer when a consumer disconnects: the bucket is reset to `max_tokens` when the consumer reconnects, and for an unbuffered consumer its bit is cleared on every chunk.

### Dispatch
**Buffered consumers (push).** On `lightning_send`, the frame is written to the socket of every connected buffered consumer that has a token. A buffered consumer without a token is skipped for that frame.

**Unbuffered consumers (pull).** The producer tracks, for each unbuffered consumer, the highest `seq_num` it has been handed (`last_seq`), and whether it is **waiting** (it has a token but no newer frame existed when it last asked for one).
- On `lightning_send`, the frame is written to a shared memory chunk and handed to every waiting consumer (taking one token from each, and clearing its waiting flag). Consumers that aren't waiting are not sent the frame at this point.
- On a returned token (`TOKEN_ACCEPT` or `TOKEN_DROP`), the producer immediately hands that consumer the newest frame with `seq_num > last_seq`. If no such frame exists yet, the consumer is marked waiting and gets the next frame sent.
- On (re)connect, the consumer starts with a full bucket, marked waiting, and gets the next frame sent. It is never handed a frame generated before it connected.

Because an unbuffered consumer only gets a chunk right before it calls `lightning_recv` again, it holds that chunk only for as long as it takes to copy it out. This means a pool of `max_tokens` chunks is rarely exhausted, and frames almost never queue up (so stale drops only happen with `max_tokens > 1`).

Both `lightning_send` and the producer's reader thread hand out frames, so each consumer's dispatch state and socket writes are protected by a per-consumer lock.

### Dynamic targets
Targets can be given at creation and added or removed at any time with `lightning_add_target` / `lightning_remove_target`, from any thread, while `lightning_send` and `lightning_recv_reply` are running.

- **Adding** tries to connect once, synchronously, so a target whose consumer is already up is usable as soon as the call returns. If the consumer isn't reachable yet, the target is still added and is retried in the background (see Reconnection).
- **Removing** closes the connection, reclaims the target's tokens, frees its bit slot and stops reconnecting. Replies already received from that consumer are still delivered by `lightning_recv_reply`.
- **Bit slots:** every target is given one of 31 bit slots when it is added, and the slot is freed when it is removed. Unbuffered consumers use it as their bit in the chunk headers. Before a slot is reused, its bit is cleared on every chunk.
- **Disconnect vs remove:** a disconnected target is still a target, and the producer keeps reconnecting to it. Only removal frees its slot, and only removing the last `shm://` target releases the chunk pool's memory (see Unbuffered connections).

### Sequence numbers and staleness
Every frame carries a `uint32_t seq_num`, which must increase monotonically with each send for a given producer (skipping values, for example on a dropped send, is fine). Staleness is measured in sequence numbers, not timestamps, so no clock synchronization is required between hosts.

Each producer sets `stale_seqs` at creation, which should be chosen based on the producer's generation frequency (for example, a 30 FPS producer that tolerates ~100 ms of queueing would use `stale_seqs = 3`). It is sent to the consumer during the handshake. When a consumer reads from a producer, it drains every frame already waiting from that producer. A frame is stale if `seq_num + stale_seqs <= newest_seq_num` from that producer. Stale frames are discarded and a `TOKEN_DROP` is returned for each. `stale_seqs = 0` disables staleness dropping.

### Identity
Every producer and consumer has an identity made up of:
- `source_name`: the name given at creation. It does not need to be unique.
- `source_id`: a random 64-bit ID generated at creation, which disambiguates endpoints with the same name.
- `source_ip`: the peer's IP address as seen by the other side, if available (empty for Unix socket connections).
- `source_host`: an optional `host_name` given at creation. It is meant for TCP connections over the internet, where the IP alone may be meaningless (NAT, proxies). It is empty if not set.

All four are exchanged during the handshake and exposed on every received `lightning_message_t`.

### Sizes
`max_send_size` always means the maximum data size of messages sent **by the side that sets it**: frames for a producer, replies for a consumer. Each side tells the other its `max_send_size` during the handshake, so buffers on the receiving side can be sized. Sending more than `max_send_size` bytes returns `LIGHTNING_ERR_TOO_LARGE`.

### Reconnection
A consumer always restarts at the same address. If a producer loses a connection to a target (broken pipe or a similar error), it drops that consumer and, until the target is removed, keeps retrying the same target address in the background with capped exponential backoff. Once it reconnects, a fresh handshake is done and the consumer starts with a full bucket.

### Threading
- Producer: `lightning_send`, `lightning_recv_reply`, `lightning_add_target` and `lightning_remove_target` may all be called concurrently from different threads (typically one sending thread and one receiving thread). Concurrent calls to `lightning_send` are serialized.
- Consumer: `lightning_recv` and `lightning_reply` must be called from the same thread. `lightning_reply` always targets the producer of the most recent frame returned by `lightning_recv`.
- `lightning_destroy_*` may be called from any thread. Any call blocked on that producer or consumer returns with `LIGHTNING_ERR_CLOSED`. After destroy returns, the handle must not be used.

## API

### void lightning_set_log_callback(lightning_log_fn fn, void* user_data)
Registers a callback that receives Lightning's internal diagnostic messages (connections, disconnects, handshake failures), with `user_data` passed through unchanged. Lightning is silent by default, and passing NULL stops logging. The callback may be called from Lightning's background threads. Set it once at startup, before other calls.

### const char* lightning_version(void)
Returns the library version string, for example `"0.1.0"`.

### lightning_producer_t* lightning_create_producer(uint32_t max_tokens, uint64_t max_send_size, uint32_t stale_seqs, const char* source_name, const char* host_name, const char** targets, lightning_error_t* error)
Creates a producer with name `source_name`. `targets` is an optional NULL-terminated list of initial targets (any mix of `tcp://`, `unix://` and `shm://`, at most 31), and each is added as if by `lightning_add_target`. Pass NULL to start with no targets. `max_tokens` is the size of each consumer's token bucket. `host_name` may be NULL.

A shared memory chunk pool is created up front (see Unbuffered connections). It only reserves address space; no memory is used until a frame is written for an `shm://` target. A background reader thread is started that receives replies and tokens from every consumer and dispatches frames to unbuffered consumers as their tokens return, and a background connector thread retries unreachable targets.

### lightning_error_t lightning_add_target(lightning_producer_t* producer, const char* address)
Adds a target (see Dynamic targets). Returns `LIGHTNING_OK` whether or not the consumer was reachable, `LIGHTNING_ERR_INVALID` if the address is malformed or is already a target, and `LIGHTNING_ERR_FULL` if the producer already has 31 targets.

### lightning_error_t lightning_remove_target(lightning_producer_t* producer, const char* address)
Removes a target (see Dynamic targets). `address` must match the string it was added with. Returns `LIGHTNING_ERR_INVALID` if it isn't a target.

### void lightning_destroy_producer(lightning_producer_t* producer)
Stops the background threads, closes all connections, unmaps the chunk pool, and frees the producer.

### lightning_error_t lightning_send(lightning_producer_t* producer, const uint8_t* data, uint64_t data_size, uint32_t seq_num)
Sends the frame to every connected buffered consumer with a token. If the producer currently has any `shm://` targets, it also publishes the frame to the chunk pool and hands it to every waiting unbuffered consumer (see Dispatch). A failure on one connection drops that consumer (see Reconnection) and does not fail the send for the others.

Returns `LIGHTNING_OK` if the frame was sent to at least one buffered consumer or published to the chunk pool. A published frame counts even if no unbuffered consumer is waiting, since one can still pull it later. Returns `LIGHTNING_ERR_DROPPED` otherwise (no buffered consumer had a token, and there are no `shm://` targets or no chunk is free). Returns `LIGHTNING_ERR_TOO_LARGE` if `data_size > max_send_size`.

### lightning_message_t* lightning_recv_reply(lightning_producer_t* producer, lightning_error_t* error)
Blocks until a reply from any consumer is available, then returns it. The `source_*` fields describe the consumer that sent it, and `seq_num` is the frame the reply answers. Token-only messages (`TOKEN_DROP`) are handled internally by the reader thread and are never returned. Returns NULL and populates `error` on failure.

### lightning_consumer_t* lightning_create_consumer(const char* source_name, const char* host_name, uint64_t max_send_size, const char* address, uint32_t reply_chunk_count, lightning_error_t* error)
Creates a consumer with name `source_name`, binds at `address`, and starts a thread that accepts connections. `max_send_size` is the maximum size of the replies this consumer sends. `host_name` may be NULL. `reply_chunk_count` is the number of reply chunks for unbuffered producers; it is ignored for a `tcp://` address, and 0 picks a default. For a Unix socket address, the reply area is created up front the same way as the producer's chunk pool, and its memory is released whenever no unbuffered producers are connected.

### void lightning_destroy_consumer(lightning_consumer_t* consumer)
Stops the accept thread, closes all connections, unmaps the reply area and all producer pools, removes the socket file for a Unix socket address, and frees the consumer.

### lightning_message_t* lightning_recv(lightning_consumer_t* consumer, lightning_error_t* error)
Blocks until a frame is available from any producer, then returns it. Producers are served round-robin regardless of mode, and the call waits with `poll`/`epoll` (no busy-waiting). When a producer is served, every frame already waiting from it is drained, stale frames are discarded with a `TOKEN_DROP` each (see Staleness), and the oldest non-stale frame is returned (the rest stay queued for the next time that producer is served). The returned message always owns its own copy of the data. Returns NULL and populates `error` if the recv fails for any reason. A producer that disconnects is removed silently, and its queued frames are discarded.

### lightning_error_t lightning_reply(lightning_consumer_t* consumer, const uint8_t* data, uint64_t data_size, uint32_t seq_num)
Sends a reply, along with a `TOKEN_ACCEPT` token, to the producer of the frame most recently returned by `lightning_recv`. `seq_num` should equal that frame's `seq_num`. Returns `LIGHTNING_ERR_BROKEN_PIPE` if that producer has disconnected, `LIGHTNING_ERR_TOO_LARGE` if `data_size > max_send_size`, and `LIGHTNING_ERR_INVALID` if `lightning_recv` has not returned a frame yet.

### void lightning_message_free(lightning_message_t* msg)
Frees a message returned by `lightning_recv` or `lightning_recv_reply`. Safe to call with NULL.

## Connection implementations (internal)

### Handshake (both modes)
The producer sends the consumer its mode (buffered or unbuffered), identity (`source_name`, `source_id`, `host_name`), `max_send_size`, and `stale_seqs`. The consumer replies with an ACK, its own identity, and its `max_send_size`. Each side records the other's IP from the socket, if available. A consumer rejects an unbuffered handshake on a `tcp://` connection.

### Buffered connections
Frames, replies and tokens are all written directly to the socket as length-prefixed messages.

### Unbuffered connections
Frame and reply data live in shared memory, and only chunk indices, sequence numbers and tokens go over the socket.

In the handshake, the producer additionally sends the consumer's bit index (its target's bit slot) and its chunk pool's memfd (via `SCM_RIGHTS`), and the consumer additionally sends its reply area's memfd.

**Producer chunk pool.** One pool per producer, shared by all of its unbuffered consumers, with `max_tokens` chunks that each fit `max_send_size` bytes. The pool is a memfd that is created and mapped once, when the producer is created, so its address never changes while `lightning_send` or the reader thread use it. Pages are only backed by memory once they are written. When the last `shm://` target is removed, the producer punches a hole over the whole memfd (`fallocate(FALLOC_FL_PUNCH_HOLE)`), which returns its memory to the kernel while keeping the mapping valid. Adding an `shm://` target later reuses the same pool. Each chunk has a 64-bit atomic header:
- 31 bits: a mask of the consumers that have been handed the frame but haven't finished reading it (bit `i` belongs to the target in bit slot `i`).
- 1 bit: the producer's write lock.
- 32 bits: the frame's sequence number.

The sequence number is read from this header, not from the frame content, so it is read atomically with the lock and the mask. A chunk is free when its lock is clear and its mask is 0. A chunk with mask 0 still holds a valid frame that can be handed out until it is reused, so the writer always reuses the free chunk with the oldest `seq_num`, which keeps the newest frames available.

- **Writing:** `lightning_send` takes the oldest free chunk by setting its write lock, writes the frame, and then publishes it with a single atomic store that sets the sequence number and clears the lock. If no chunk is free, the frame is not published.
- **Handing out:** handing a frame to consumer `i` is a compare-and-swap on the header from `(mask, lock = 0, seq = S)` to `(mask | bit_i, 0, S)`, followed by sending the chunk index and `seq_num` over the socket. If the CAS fails (the writer took the chunk to reuse it), the producer rescans for the newest frame.
- **Reading:** the consumer checks that the header's sequence number matches the one it was sent, copies the frame out, and then clears its bit (release ordering). Stale frames have their bit cleared without being copied. Once a consumer's bit is set, the chunk cannot be reused until the consumer clears it, so a consumer can never read a torn frame. As defense in depth, the header is checked again after the copy (seqlock style). If the producer reclaimed the chunk in the meantime, which only happens once it considers the connection dead, the frame is discarded instead of being returned torn.

**Consumer reply area.** One area per consumer, shared by all of its unbuffered producers, with `reply_chunk_count` chunks that each fit `max_send_size` bytes. It is created and mapped once, when the consumer is created, and its memory is released with the same hole-punching trick whenever the last unbuffered producer disconnects. Each chunk has a 64-bit atomic header:
- 32 bits: the sequence number of the frame that generated the reply.
- 32 bits: the owner, which is 0 when the chunk is free, or otherwise the internal ID of the producer connection the reply was sent to.

`lightning_reply` writes the reply to a free chunk (setting its owner) and sends the chunk index, along with a `TOKEN_ACCEPT` token, to the producer. If no chunks are free, it waits (with a short sleep and backoff) until the producer frees one, so replies are never lost. The producer's reader thread copies each reply out, checks that the header didn't change during the copy, and then frees the chunk, so a consumer is never blocked by a producer that is slow to call `lightning_recv_reply`. If a producer disconnects (broken pipe), every reply chunk it owns is freed, so a dead producer can never hold chunks forever.

### consumer_accept(lightning_consumer_t* consumer)
Runs in the consumer's accept thread. Blocks until a new connection comes in, runs the handshake, picks the buffered or unbuffered implementation based on the mode the producer declared, and hands the resulting producer connection to `lightning_recv`'s thread through a mutex-protected list. Only that thread ever frees a producer connection. For an unbuffered producer, it also maps the producer's chunk pool (unmapped when that producer is removed). An eventfd wakes a blocked `lightning_recv` so it can start polling the new connection. When a send, recv, or reply fails with broken pipe or a similar error, that producer is removed from the list.

### producer_connect(lightning_producer_t* producer, uint32_t target_index)
Connects to (or reconnects to) a single target using the implementation its prefix selects, and runs the handshake with a timeout. It is called once by `lightning_add_target` and then by the connector thread's retry loop. For an unbuffered target, on a disconnect and before reconnecting, the producer atomically clears bit `target_index` on every chunk so those chunks can be reused.

## Message Definitions

```c
typedef enum {
  LIGHTNING_TOKEN_NONE = 0,
  LIGHTNING_TOKEN_ACCEPT = 1,
  LIGHTNING_TOKEN_DROP = 2,
} lightning_token_t;

typedef struct lightning_message_t {
  const char *source_name;  /* sender's source_name */
  uint64_t source_id;       /* sender's random unique ID */
  const char *source_ip;    /* sender's IP, "" if unavailable (Unix socket) */
  const char *source_host;  /* sender's host_name, "" if not set */
  uint32_t seq_num;         /* frame seq_num (or the frame a reply answers) */
  lightning_token_t token;  /* ACCEPT on replies, NONE on frames */
  uint64_t data_size;
  uint8_t *data;
} lightning_message_t;
```

## Errors

```c
typedef enum {
  LIGHTNING_OK = 0,
  LIGHTNING_ERR_DROPPED,      /* send: no consumer could take the frame */
  LIGHTNING_ERR_TOO_LARGE,    /* data_size > max_send_size */
  LIGHTNING_ERR_BROKEN_PIPE,  /* the peer disconnected */
  LIGHTNING_ERR_INVALID,      /* bad address/argument, or reply before recv */
  LIGHTNING_ERR_FULL,         /* add_target: already at 31 targets */
  LIGHTNING_ERR_CLOSED,       /* the handle was destroyed */
  LIGHTNING_ERR_INTERNAL,     /* internal failure catch-all */
} lightning_error_t;
```
