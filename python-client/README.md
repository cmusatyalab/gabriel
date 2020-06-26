# Gabriel Python Module

Python module for communicating with a [Gabriel Server](https://github.com/cmusatyalab/gabriel-server-common).

## Installation
Requires Python >= 3.5

Run `pip install gabriel-client`

## Usage

Create an instance of `server_comm.WebsocketClient`. Then call
the `launch()` method. The constructor to `WevsocketClient` takes `host`,
`port`, `producer_wrappers` (a list of
`server_comm.ProducerWrapper` instances), and `consumer` (a
function called whenever a new result is available).

`server_comm.opencv_adater.OpencvAdapter` provides
`producer_wrappers` and a consumer.
`early_discard_filter.Filter` provides producer wrappers.
Use of either of these classes is optional. You can define your own producers
and/or a consumer, and just use `WebsocketClient` with these. `OpencvAdapter` is
intended for clients that send image frames from a webcam or a video file,
without doing early discard. `OpencvAdapter.consumer()` decodes images returned
by the server and then calls the `consume_frame` function that was passed to the
`OpencvAdapter`'s constructor. This consumer will not work when a
`result_wrapper` contains more than one result, or a result that is not an
image. However, you can still use the producer from `OpencvAdapter` and write
your own custom consumer.

If you choose to write your own `ProducerWrapper`, you must pass a
[coroutine function](https://docs.python.org/3/glossary.html#term-coroutine-function)
as the `producer` argument to the constructor of `ProducerWrapper`. The
`producer` is run on an
[asyncio event loop](https://docs.python.org/3/library/asyncio-eventloop.html#event-loop),
so it is important that the `producer` does not call any function that could
block. This would cause the whole event loop to block.

If you need to run blocking code to get an input for Gabriel, you can use
`early_discard_filter.Filter`. The `early_discard_filter` module was created for
sending frames that passed early discard filters. But it can still be used for
other cases. Create an instance of `Filter` and include the `ProducerWrapper`
returned from `Filter.get_producer_wrapper()` in the list of `producer_wrappers`
you pass to `server_comm.WebsocketClient`. You can then pass the `Filter`
instance to a separate process started using the `multiprocessing` module. When
results are ready, send them with `Filter.send()`. `Filter.send()` should only
ever be called from one process. Create at least one `Filter` per process that
you want to send from. Frames sent with `Filter.send()` are not guaranteed to be
sent to the server. As soon as a token becomes available, the most recent unsent
frame will be sent. If `Filter.send()` is called multiple times before a token
becomes available, only the most recent frame will actually be sent to the
server. If a token becomes available before the next frame, Gabriel
will send the next frame after `Filter.send()` is called. `Filter` will not
block the event loop.

If you want the client to ignore results, you can pass
`early_discard_filter.consumer` as the `consumer` argument to `WebsocketClient`.

`WebsocketClient` does not run producers until there is a token available to
send a result from them. This guarantees that producers are not run more
frequently than they need to be, and when results are sent to the server, they
are as recent as possible. However, running the producer introduces a delay
between when a token comes back and when the next frame is sent. `Filter` allows
frames to be generated asynchronously from tokens returning. The two downsides
to this approach are:
1. Some frames might be generated and never sent.
2. When a token does come back, the last frame sent to a `Filter` might have
   been generated a while ago. In practice, hopefully tokens will be returned to
   the client at a reasonable rate.

If you want to measure average round trip time (RTT) and frames per second
(FPS), use `timing_client.TimingClient` in place of `WebsocketClient`. FPS
information will be printed automatically, every `output_freq` frames. Average
RTT will be printed when `TimingClient.compute_avg_rtt()` is called.

## Examples

1. The OpenRTiST
   [capture adapter](https://github.com/cmusatyalab/openrtist/blob/dfc3e246031a3006bdf0f5fcaa192ed0a5237ab8/python-client/capture_adapter.py#L7)
   uses `WebsocketClient`.
2. The OpenRTiST [playback stream](https://github.com/cmusatyalab/openrtist/blob/master/python-client/playback_stream.py)
   uses `WebsocketClient` and `TimingClient`.


## Future Improvements

1. Log RTT information in `TimingClient.consumer()`. The Android client does
   this
   [here](https://github.com/cmusatyalab/gabriel-android-common/blob/4a85b0650611b47dc5d07afd934c74037fe1d55d/client/src/main/java/edu/cmu/cs/gabriel/client/comm/MeasurementServerComm.java#L42).

## Publishing Changes to PyPi

Update the version number in setup.py. Then follow [these instructions](https://packaging.python.org/tutorials/packaging-projects/#generating-distribution-archives).
