# gabriel

A C++/GStreamer/FlatBuffers core with a Go binding layer, built as a
single Go module (no separate C++ build system — cgo compiles the C++
directly).

## Layout

- `api/` — the FlatBuffers schema (`greeting.fbs`) and its generated
  C++ (`api/cpp/`) and Go (`api/go/fbs/`) code, checked into git.
  Regenerate with `go generate ./api/...` after editing the schema
  (requires `flatc` on `PATH`).
- `internal/ccore/` — the only package that compiles C++. Wraps the
  GStreamer-linked FlatBuffers core with a small Go API.
- `gabriel.go` (root package `gabriel`) — the public API.
- `cmd/roundtrip-example/` — a runnable demo of the C++-to-Go FlatBuffer
  roundtrip.

## Building

Requires a C++17 compiler, `libgstreamer1.0-dev` (or equivalent), and
`libflatbuffers-dev` (or equivalent, for `flatbuffers/flatbuffers.h`).
`flatc` is only needed if you're regenerating the schema output, not for
a normal build.

    go build ./...
    go test ./...
    go run ./cmd/roundtrip-example
