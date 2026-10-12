# Comparing with the old Gabriel

This measures how much each Gabriel itself costs when everything runs on one
machine. An app sends a raw RGB frame, an engine answers it at once with 256
bytes, and the app sends the next frame when the answer arrives. One frame is
in flight at a time (one token). The engine does no work, so the round trip is
all framework: copies, serialization, wake-ups.

| System                       | App to server                 | Server to engine |
| ---------------------------- | ----------------------------- | ---------------- |
| `new`                        | shared memory                 | shared memory    |
| `old-websocket`              | WebSocket over TCP            | gRPC over TCP    |
| `old-websocket-uncompressed` | as above, without compression | gRPC over TCP    |
| `old-tcp`                    | ZeroMQ over TCP               | gRPC over TCP    |
| `old-ipc`                    | ZeroMQ over a Unix socket     | gRPC over a Unix socket |

Each system runs as three processes: a server, an engine and an app. The
frames are 720p, 1080p and 4K (2.8, 6.2 and 24.9 MB).

## Running it

```sh
cargo build --release -p gabriel              # from the repository root
pip install -r requirements.txt               # from this directory
python compare.py                             # or e.g. --systems new old-ipc --resolutions 4k
```

It prints a table and saves it, with every round trip, under
`bench-results/compare-<unix time>/`. Columns:

- `p50_ms`, `p90_ms`, `p99_ms`: round trip percentiles, from sending a frame
  to receiving its answer.
- `fps`: frames a second. With one frame in flight, this is 1 / round trip.
- `server_cpu_pct`, `engine_cpu_pct`, `app_cpu_pct`: the CPU each process
  used, as a percentage of one core.

## What we changed in the old Gabriel

- **The old engine could not receive frames over 4 MB.** gRPC limits messages
  to 4 MB by default, and the old `EngineRunner` does not raise the limit. We
  raise it in the engine process, so the 1080p and 4K runs work.
- **`old-websocket-uncompressed` turns compression off.** The `websockets`
  library compresses every message by default, and the old client has no option
  to turn this off. That cost dominates `old-websocket`, so we also measure
  without it.

## What it found

The results, on one machine, are in `docs/evaluation.tex` ("Comparison with
the original Gabriel"). One finding is about the new Gabriel itself: **most of
its round trip is spent copying the frame.** On the local path, Gabriel copies
each frame four times:

| Process | Where                                                | Copy                                      |
| ------- | ---------------------------------------------------- | ----------------------------------------- |
| app     | `rust/src/client/producers.rs`, `Arc::from(data)`     | producer's sample into the heap           |
| app     | `rust/src/client/local_server.rs`, `write_from_slice` | heap into the sample for the server       |
| server  | `rust/src/server/dispatch.rs`, `Arc::from(bytes)`     | server's sample into the heap             |
| server  | `rust/src/server/dispatch.rs`, `write_from_slice`     | heap into the engine's input sample       |

The server's two copies cost 0.30, 0.73 and 2.5 ms of CPU per frame at 720p,
1080p and 4K. That is the `server_cpu_pct` column: the server uses about 1.5%
of a core when idle. The design note (`docs/dag-tokens.tex`, "Copies") gives
two fixes: hold the received samples instead of copying them (half the
copies), or zero copy on one machine (all of them).
