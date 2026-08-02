# Gabriel Server Library

## Installation

Requires Python 3.10 or newer.

Run `pip install gabriel-server`

## Usage

Data is processed by Cognitive Engines. Each cognitive engine is implemented in
a separate class that inherits `cognitive_engine.Engine`. The `handle` method is
called each time there is a new frame for the engine to process. `handle` gets
passed an
[`InputFrame`](https://github.com/cmusatyalab/gabriel/blob/main/protocol/proto/gabriel_protocol/v1/gabriel.proto)
and a `client_info` (the `Any` registered by the producing client's
`Registration` message, or an empty `Any` if it registered none). It must
return a `cognitive_engine.Result`, a dataclass with a `status`
(`gabriel_pb2.Status`) and an optional `payload` (a `str`, `bytes`, or
`google.protobuf.any_pb2.Any`). The client will get a token back as soon as
`handle` returns a `Result` (even if its `payload` is left unset, in which case
the client will not receive a result). Therefore, returning from `handle`
before the engine is ready for the next frame will cause the engine to get
saturated with requests faster than they can be processed.

### Single Engine Workflows

The simplest possible setup involves a single cognitive engine. In this case,
the Gabriel Server and the cognitive engine are run in the same Python program,
using `local_engine.LocalEngine`:

```python
local_engine.LocalEngine(
    engine_factory=lambda: MyEngine(),
    engine_id='my_engine',
    input_queue_maxsize=60,
    port=9099,
    num_tokens=2,
).run()
```

`engine_factory` should be a function that runs the constructor for the
cognitive engine. A separate process gets created with Python's
`multiprocessing` module, and `engine_factory` gets executed in this process.
Having `engine_factory` return a reference to an object that was created before
`local_engine.LocalEngine.run` was called is not recommended.

By default, `LocalEngine` accepts client connections over WebSocket; pass
`use_zeromq=True` to use ZeroMQ instead, or `ipc_path` to listen on a Unix
domain socket rather than TCP.

### Multiple Engine Workflows

When a workflow requires more than one cognitive engine, the Gabriel server must
be run as a standalone Python program, using
`network_engine.server_runner.ServerRunner`. Each cognitive engine is run as an
additional separate Python program, using
`network_engine.engine_runner.EngineRunner`. The cognitive engines can be run on
the same computer that the Gabriel server is running on, or a different
computer. Under the hood, the server communicates with cognitive engines over
gRPC.

The Gabriel server is run as follows:

```python
server_runner.ServerRunner(
    client_endpoint=9099,
    engine_endpoint=9098,
    num_tokens=2,
    input_queue_maxsize=60,
).run()
```

`client_endpoint` accepts connections from clients, and `engine_endpoint`
accepts connections from cognitive engines over gRPC. By default, clients also
connect over gRPC; pass `client_transport=Transport.WEBSOCKET` or
`Transport.ZEROMQ` (from the `network_engine.server_runner.Transport` enum) to
use a different client-facing transport instead. `use_client_ipc` and
`use_engine_ipc` switch the respective endpoint to a Unix domain socket path
instead of a TCP port. `prometheus_port` (default 8000) controls where
Prometheus metrics are served.

Cognitive engines are run as follows:

```python
engine_runner.EngineRunner(
    engine=MyEngine(),
    engine_id='my_engine',
    server_address='localhost:9098',
    all_responses_required=True,
).run()
```

`server_address` is the gRPC target of the server's engine-facing endpoint
(`host:port`), not a ZeroMQ URI. Note that `engine` should be a reference to an
existing engine, not a function that runs the constructor for the engine.
Unlike `local_engine.LocalEngine`, `network_engine.engine_runner.EngineRunner`
does not run the engine in a separate process.

When `all_responses_required` is False, the client will not receive a result
from this engine if a different engine processing the same frame already
returned a result for this frame. When `all_responses_required` is True,
the server will send every result this engine returns. Typically, you should set
`all_responses_required` to True when an engine returns results to the clients,
and False when an engine stores results but does not include anything useful for
the client in the `Result` instance that it returns.

The server should be started before the engine runner.

#### TLS

Both `ServerRunner` and `EngineRunner` accept optional TLS/mutual-TLS
arguments. `ServerRunner` takes `tls_cert` and `tls_key` (a PEM certificate
chain and private key presented by the server's gRPC endpoints), and
`tls_client_ca_cert` (a PEM CA certificate used to verify client/engine
certificates, enabling mutual TLS). `EngineRunner` takes the corresponding
client-side options: `tls_ca_cert` (to verify the server's certificate) and
`tls_client_cert`/`tls_client_key` (presented to the server for mutual TLS). If
these are omitted, connections are made over plaintext gRPC.

#### Timeouts

`ServerRunner`'s engine-facing gRPC connections and `EngineRunner`'s connection
to the server both use gRPC keepalive pings to detect a dead connection (for
example, a crashed engine process or a network partition) without relying on
frames being sent.

`EngineRunner` takes optional `timeout` and `request_retries` arguments.
`timeout` (default 10 seconds) is how long the runner waits for its channel to
the server to become ready. `request_retries` (default 3) specifies the number
of attempts that this runner will make to re-establish a lost connection with
the Gabriel server. The number of retry attempts do not get replenished at any
point during the engine runner's execution. The default `timeout` and
`request_retries` values should be sufficient for most configurations.

## Publishing Changes to PyPi

Bump the `version` field in `pyproject.toml`, then push a tag of the form
`server/vX.Y.Z`. The `publish-gabriel-server.yml` GitHub Actions workflow
builds and publishes the package to PyPI automatically.
