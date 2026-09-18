// Package api holds the Gabriel FlatBuffers schema and its generated code.
package api

// flatc's -o flag is global, not per-language-flag, so --cpp and --go need
// separate invocations or the second -o silently wins for both outputs.
//
// Python omits --grpc: flatc's Python gRPC generator has never worked (fails
// with "Unable to generate GRPC interface for Python" on every released
// version, including current ones - see google/flatbuffers#8325). The
// Python gRPC layer is hand-written instead; see api/python/greeter_grpc.py.
//go:generate flatc --cpp --grpc -o cpp greeting.fbs
//go:generate flatc --go --grpc -o go greeting.fbs
//go:generate flatc --python -o python greeting.fbs
