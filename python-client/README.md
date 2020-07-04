# Gabriel Python Module

## Installation

Requires Python 3.5 or later.

Run `pip install gabriel-client`

## Usage

Create an instance of `websocket_client.WebsocketClient`. Then call
the `launch()` method. The constructor to `WebsocketClient` takes `host`,
`port`, `producer_wrappers` (a list of
`websocket_client.ProducerWrapper` instances), and `consumer` (a
function called whenever a new result is available).

`opencv_adater.OpencvAdapter` provides `producer_wrappers` and a consumer.
`push_source.Source` provides producer wrappers.
Use of either of these classes is optional. You can define your own producers
and/or a consumer, and just use `WebsocketClient` with these. `OpencvAdapter` is
intended for clients that send image frames from a webcam or a video file,
without doing early discard. `OpencvAdapter.consumer` decodes images returned
by the server and then calls the `consume_frame` function that was passed to the
`OpencvAdapter`'s constructor. This consumer will not work when a
`ResultWrapper` contains more than one result, or a result that is not an
image. However, you can still use the producer from `OpencvAdapter` and write
your own custom consumer. The `OpencvAdapter` adapter requires OpenCV to be
installed and accessible to Python. The
[opencv-python](https://pypi.org/project/opencv-python) package is a convenient
way to install OpenCV for Python. If you do not use `OpencvAdapter`, you do not
have to have OpenCV installed.

If you choose to write your own `ProducerWrapper`, you must pass a
[coroutine function](https://docs.python.org/3/glossary.html#term-coroutine-function)
as the `producer` argument to the constructor of `ProducerWrapper`. The
`producer` is run on an
[asyncio event loop](https://docs.python.org/3/library/asyncio-eventloop.html#event-loop),
so it is important that the `producer` does not call any function that could
block. This would cause the whole event loop to block.

If you need to run blocking code to get an input for Gabriel, you can use
`push_source.Source`. You should also use `Source` whenever you want to run the
code to produce a frame before a token is available. `Source` should always be
used for sending frames that pass early discard filters. Create an instance of
`Source` and include the `ProducerWrapper` returned from
`Source.get_producer_wrapper()` in the list of `producer_wrappers`
you pass to the constructor of `WebsocketClient`. You can then pass the `Source`
instance to a separate process started using the `multiprocessing` module. When
results are ready, send them with `Source.send()`. `Source.send()` should only
ever be called from one process. Create at least one `Source` per process that
you want to send from. Frames sent with `Source.send()` are not guaranteed to be
sent to the server. As soon as a token becomes available, the most recent unsent
frame will be sent. If `Source.send()` is called multiple times before a token
becomes available, only the most recent frame will actually be sent to the
server. If a token becomes available before the next frame is ready, Gabriel
will send the next frame after `Source.send()` is called. `Source` will not
block the event loop.

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
2. When a token does come back, the last frame sent to a `Source` might have
   been generated a while ago. In practice, hopefully tokens will be returned to
   the client at a reasonable rate.

If you want to measure average round trip time (RTT) and frames per second
(FPS), use `measurement_client.MeasurementClient` in place of `WebsocketClient`.
average RTT and FPS information will be printed automatically, every
`output_freq` frames.

## Examples

1. The round trip example
   [client](https://github.com/cmusatyalab/gabriel/blob/2840808c3d90e4980969b2744877e739723c84bb/examples/round_trip/client.py#L41)
   uses `OpencvAdapter`.
2. The one way example
   [producer client](https://github.com/cmusatyalab/gabriel/blob/2840808c3d90e4980969b2744877e739723c84bb/examples/one_way/producer_client.py#L44)
   uses a custom producer.
3. The one way example
   [push client](https://github.com/cmusatyalab/gabriel/blob/2840808c3d90e4980969b2744877e739723c84bb/examples/one_way/push_client.py#L34)
   uses `push_source.Source`.
4. The OpenRTiST
   [playback stream client](https://github.com/cmusatyalab/openrtist/blob/019a58999fbdd7494b09b141e2c688e2fda32fb0/python-client/playback_stream.py#L35)
   uses `MeasurementClient`.

## Publishing Changes to PyPi

Update the version number in setup.py. Then follow [these instructions](https://packaging.python.org/tutorials/packaging-projects/#generating-distribution-archives).
