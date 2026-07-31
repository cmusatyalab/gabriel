# Gabriel Python Module

Full documentation is available at [cmusatyalab.github.io/gabriel](https://cmusatyalab.github.io/gabriel/).

## Installation

Requires Python 3.10 or later.

Run `pip install gabriel-client`

## Usage

Create an instance of `websocket_client.WebsocketClient`. Then call the
`launch()` method (or `launch_async()` if you are already running an asyncio
event loop). The `WebsocketClient` constructor's arguments are `server_endpoint`
(a `ws://host:port` URI), `input_producers` (a list of
`gabriel_client.InputProducer` instances), `consumer` (a function called
whenever a new result is available), and optionally `prometheus_port` (port
for Prometheus metrics, default 8001), `client_info` (an `Any` proto sent to
the server at registration and forwarded to cognitive engines), and
`registration_retry_interval_seconds` (how long to wait for a `Registered`
acknowledgement before retrying registration).

Gabriel also ships `grpc_client.GrpcClient` and `zeromq_client.ZeroMqClient`,
which offer the same interface as `WebsocketClient` but communicate with the
server over gRPC or ZeroMQ respectively. Use whichever matches the transport
the server is configured for.

`opencv_adapter.OpencvAdapter` provides input producers (via
`get_producer_wrappers()`) and a consumer. `push_source.Source` provides an
input producer (via `get_input_producer()`). Use of either of these classes is
optional. You can define your own producers and/or a consumer, and just use
`WebsocketClient` with these. `OpencvAdapter` is intended for clients that send
image frames from a webcam or a video file, without doing early discard.
`OpencvAdapter.consumer` decodes images returned by the server and then calls
the `consume_frame` callback that was passed to the `OpencvAdapter`'s
constructor. This consumer will not work when a result contains a payload that
is not an image. However, you can still use the producer from `OpencvAdapter`
and write your own custom consumer. The `OpencvAdapter` requires OpenCV to be
installed and accessible to Python. The
[opencv-python](https://pypi.org/project/opencv-python) package is a convenient
way to install OpenCV for Python. If you do not use `OpencvAdapter`, you do not
have to have OpenCV installed.

If you choose to write your own `InputProducer`, you must pass a
[coroutine function](https://docs.python.org/3/glossary.html#term-coroutine-function)
as the `producer` argument to the constructor of `InputProducer`, along with
`target_engine_ids` (the engines this producer's frames should be sent to) and
an optional `producer_name`. The `producer` is run on an
[asyncio event loop](https://docs.python.org/3/library/asyncio-eventloop.html#event-loop),
so it is important that the `producer` does not include any blocking code. This
would cause the whole event loop to block. `InputProducer` instances can be
stopped and resumed at runtime with `stop()`/`resume()`, and their target
engines can be changed at runtime with `change_target_engines()`,
`add_target_engine()`, and `remove_target_engine()`; these methods are
thread-safe.

If you need to run blocking code to get an input for Gabriel, you can use
`push_source.Source`. You should also use `push_source.Source` whenever you want
to run the code to produce a frame before a token is available.
`push_source.Source` should always be used for sending frames that pass early
discard filters. Create an instance of `push_source.Source` (passing a
`producer_name` and `target_engine_ids`) and include the `InputProducer`
returned from `push_source.Source.get_input_producer()` in the list of
`input_producers` you pass to the constructor of `WebsocketClient`. You can
then pass the `push_source.Source` instance to a separate process started
using the `multiprocessing` module. When results are ready, send them with
`push_source.Source.send()`. `push_source.Source.send()` should only ever be
called from one process. Create at least one `push_source.Source` per process
that you want to send frames from. Frames sent with
`push_source.Source.send()` are not guaranteed to be sent to the server. As
soon as a token becomes available, the most recent unsent frame will be sent.
If `push_source.Source.send()` is called multiple times before a token becomes
available, only the most recent frame will actually be sent to the server. If a
token becomes available before the next frame is ready, Gabriel will send the
next frame after `push_source.Source.send()` is called. `push_source.Source`
will not block the event loop.

If you want the client to ignore results, you can pass
`push_source.consumer` as the `consumer` argument to `WebsocketClient`.

`WebsocketClient` does not run producers until there is a token available to
send a result from them. This guarantees that producers are not run more
frequently than they need to be, and when results are sent to the server, they
are as recent as possible. However, running the producer introduces a delay
between when a token comes back and when the next frame is sent.
`push_source.Source` allows frames to be generated asynchronously from tokens
returning. The two downsides to this approach are:
1. Some frames might be generated and never sent.
2. When a token does come back, the last frame sent to a `push_source.Source`
   instance might have been generated a while ago. In practice, hopefully tokens
   will be returned to the client at a reasonable rate.

If you want to measure average round trip time (RTT) and frames per second
(FPS), use `measurement_client.MeasurementClient` in place of `WebsocketClient`.
average RTT and FPS information will be printed automatically, every
`output_freq` frames.

## Examples

1. The round trip
   [client](https://github.com/cmusatyalab/gabriel/blob/main/examples/round_trip/client.py)
   uses `OpencvAdapter`.
2. The one way
   [producer client](https://github.com/cmusatyalab/gabriel/blob/main/examples/one_way/producer_client.py)
   uses a custom producer.
3. The one way
   [push client](https://github.com/cmusatyalab/gabriel/blob/main/examples/one_way/push_client.py)
   uses `push_source.Source`.
4. The OpenRTiST
   [playback stream client](https://github.com/cmusatyalab/openrtist/blob/019a58999fbdd7494b09b141e2c688e2fda32fb0/python-client/playback_stream.py#L35)
   uses `MeasurementClient`.

## Publishing Changes to PyPi

Bump the `version` field in `pyproject.toml`, then push a tag of the form
`python-client/vX.Y.Z`. The `publish-gabriel-client.yml` GitHub Actions
workflow builds and publishes the package to PyPI automatically.
