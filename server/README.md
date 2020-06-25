# Gabriel Server Library

## Usage

You must run this using Python 3.5 or newer. Install by running
```bash
pip install gabriel-server
```

Data is processed by Cognitive Engines. Each cognitive engine is implemented in
a separate class that inherits `cognitive_engine.Engine`. The `handle` method is
called each time there is a new frame for the engine to process. `handle` gets
passed a
[`FromClient`](https://github.com/cmusatyalab/gabriel/blob/4d02fa9af3dee5781bb2cfdf66b2f15d60682bcf/protocol/gabriel.proto#L20).
It must return a
[`ResultWrapper`](https://github.com/cmusatyalab/gabriel/blob/4d02fa9af3dee5781bb2cfdf66b2f15d60682bcf/protocol/gabriel.proto#L30).
If there are no results that should be returned to the client (which might be
the case for a cognitive engine that writes results to a database), `handle`
should return a `ResultWrapper` with an empty `results` list, when the engine is
ready to start processing the next frame. The client will get a token back as
soon as `handle` returns a `ResultWrapper`. Therefore, returning from `handle`
before the engine is ready for the next frame will cause the engine to get
saturated with requests faster than they can be processed.

### Single Engine Workflows

The simplest possible setup involves a single cognitive engine. In this case,
the Gabriel Server and the cognitive engine are run in the same Python program.
Start the engine and server as follows:

```python
local_engine.run(engine_factory=lambda: MyEngine(), filter_name='my_filter',
                 input_queue_maxsize=60, port=9099, num_tokens=2)
```

`engine_factory` should be a function that runs the constructor for the
cognitive engine. A separate process gets created with Python's
`multiprocessing` module, and `engine_factory` gets executed in this process.
Having `engine_factory` return a reference to an object that was created before
`local_engine.run` was called is not recommended.

### Multiple Engine Workflows

When a workflow requires more than one cognitive engine, the Gabriel server must
be run as a standalone Python program. Each cognitive engine is run as an
additional separate Python program. The cognitive engines can be run on the same
computer that the Gabriel server is running on, or a different computer. Under
the hood, the server communicates with the cognitive engines using
[ZeroMQ](https://zeromq.org/).

The Gabriel server is run using `network_engine.server_runner` as follows:

```python
server_runner.run(websocket_port=9099, zmq_address='tcp://*:5555', num_tokens=2,
                  input_queue_maxsize=60)
```

Cognitive engines are run using `network_engine.engine_runner` as follows:

```python
engine_runner.run(engine=MyEngine(), filter_name='my_filter',
                  server_address='tcp://localhost:5555')
```

Note that `engine` should be a reference to an existing engine, not a function
that runs the constructor for the engine. Unlike `local_engine`,
`network_engine.engine_runner` does not run the engine in a separate process.

The server should be started before the engine runner.

#### Timeouts

When setting timeout values, consider the following from
[ZeroMQ's guide](http://zguide.zeromq.org/py:chapter4#Shrugging-It-Off):
> If we use a TCP connection that stays silent for a long while, it will, in
> some networks, just die. Sending something (technically, a "keep-alive" more
> than a heartbeat), will keep the network alive.

`server_runner.run` takes an optional `timeout` argument. The default value of
five seconds should be sufficient unless one of your cognitive engines might
take more than five seconds to process a frame. This `timeout` value
should be set to the longest amount of time that any of your cognitive engines
could take to process a frame. The engine runner will not send or reply to
messages while the cognitive engine is in the middle of processing a frame.

`engine_runner.run` takes optional `timeout` and `request_retries` parameters.
`request_retries` specifies the number of attempts that this runner will make to
reestablish a lost connection with the Gabriel server. The number of retry
attempts do not get replenished at any point during the engine runner's
execution. The default `timeout` and `request_retries` values should be
sufficient for most configurations.

## High Level Design

Each early discard filter should send one frame at a time. Every output from an
early discard filter should have the same type of data, and this type
should not change. For example, if a filter sends images, it should only ever
send images, and it should not also include audio along with an image. Audio and
images should be sent by two different filters. `FromClient` messages have an
`extras` field that can be used to send metadata, such as GPS and IMU
measurements, or app state. Embedding binary data to circumvent the
"one type of media per filter" restriction will likely lead to cognitive
engines that are difficult for other people to maintain. Multiple payloads can
be sent in a single `FromClient` message. This is intended for cases where an
input to a filter must contain several consecutive images. A single `FromClient`
message should represent one single input to a cognitive engine.

Each client has one set of tokens per early discard filter. This allows the
client to send frames that have passed "filter x" at a different rate than it
sends frames that have passed "filter y." A cognitive engine can only consume
frames that have passed one filter. A cognitive engine cannot change the
filter that it consumes frames from. Multiple cognitive engines can consume
frames that pass the same filter.

The Gabriel server returns a token to the client for "filter x" as soon as the
first cognitive engine that consumes frames from "filter x" returns a
`ResultWrapper` for that frame. When a second cognitive engine that also
consumes frames from "filter x" returns a `ResultWrapper` for the same frame,
the Gabriel server does not return a second token to the client. If the
`ResultWrapper` from the second cognitive engine has an empty `results` list,
the server will not send anything to the client in response to this
`ResultWrapper`. If the `ResultWrapper` contains a non-empty `results` list, the
server will send the `ResultWrapper` to the client, but it will not return a
token (because it already returned the token for this frame with the result from
the first cognitive engine).

Cognitive engines might not receive every frame sent to the server. In
particular, the client will send frames to the server at the rate that the
fastest cognitive engine can process them. Slower engines that consume frames
from the same filter might miss some of the frames that were given to the
fastest engine. After an engine finishes processing its current frame, it will
be given the most recent frame that was given to the fastest engine. When the
first engine completes the most recent frame, a new frame will be taken off the
input queue and given to the fastest engine.

When the client is not performing early discard, set the filter name as
something that describes the application (for example "openrtist").

### Flow Control

Gabriel's flow control is based on tokens. When the client sends a frame to the
server, this consumes a token for the filter that the frame passed. When the
first cognitive engine finishes processing this frame, the client gets back the
token that was consumed sending the frame. This ensures that frames are sent to
the server at the rate that the fastest engine processes them. If the server
runs into an error processing a frame, it immediately sends a message to the
client indicating the return of a token.

The client will only send a new frame after it receives a token back. This can
lead to periods where the server has no input when the latency between clients
and the server is high. Setting a high number of tokens will fill up the queue
of inputs on the server and thus reduce the length of these idle periods.
However, the frames in the queue might be stale by the time they
get processed. You should not set the number of tokens above two, unless the
latency between clients and the server is high, and your workload is not latency
critical.

Each `FromClient` messages sent consumes one token. A `ToClient` message with
`return_token` set to true indicates the return of one token. Specifying the
specific number of tokens that a client has for a filter would lead to race
conditions based on the order that the client and server send and receive
messages. Representing the consumption or return of a single token in a message
avoids this problem. Clients communicate with the server using
[The WebSocket Protocol](https://tools.ietf.org/html/rfc6455), which uses TCP.
Therefore, we assume that messages are delivered reliably and in order.

## Future Improvements

1. If two filters both send the same payload, the payload will be sent to
   the server twice. Caching payloads, and referencing the cached item in
   subsequent `FromClient` messages would save bandwidth.
2. We allow multiple different cognitive engines to consume frames that have
   passed the same early discard filter. However, there is no way to have
   multiple instances of the same engine. In particular, if there
   were multiple cognitive engines that performed face recognition, we would not
   want more than one of them to process the same frame. We need some way
   to decide which instance of an engine should process a given frame. For each
   group of engines, there should be a way to toggle between the following
   options:
   1. Each request can go to a different engine. There should be a scheme to
      load balance individual requests (such as a simple round robin). This is
      the best option for engines that do not store any state information. Note
      that if the amount of state needed for each client is small, the client
      and engine can pass it back and forth to each other in the `extras` field
      of `FromClient` and `FromEngine` messages. This would allow the client's
      frames to be processed by any instance of a given engine. However, your
      client code needs to ignore results based on frames that the client sent
      before it received the latest state update.
   2. Each client is assigned to a specific instance of the engine. No other
      instances of the engine will get frames from this client. This setting
      will be used for engines that store state information for each client.
3. Gabriel does not expose any client identification information to cognitive
   engines. Clients can include this information in the `extras` field of
   `FromClient` messages. However, this should be added as a part of Gabriel
   itself at some point.
   1. Should this identity persist when the client disconnects and reconnects?
   2. If support for multiple instances of the same engine is added, should this
      identity be used when a group is set to assign a client to one specific
      instance of an engine?
4. When using the `network_engine` modules, the Python and Android clients do
   not handle the case when all engines that consume a filter disconnect. To
   handle this case, the Gabriel server should tell all clients when all engines
   for a certain filter disconnect. Then the clients need to remove their tokens
   for this filter.
5. `local_engine` sends results from the process running the cognitive engine to
   the process running the websocket server using `os.pipe()`. The
   [early_discard_filter.py](https://github.com/cmusatyalab/gabriel/blob/master/python-client/src/gabriel_client/early_discard_filter.py)
   script in the Python client does something similar. This isn't the cleanest
   approach. Perhaps we should switch to one of the following:
   1. Send results to the websocket server process using
      `multiprocessing.pipe()`. Reading from this pipe directly in the event
      loop will block it. But we could watch the appropriate file descriptor
      using the `asyncio` event loop's `add_reader` function. Another option
      would be to use the `asyncio` event loop's `run_in_executor` method with a
      `concurrent.futures.ThreadPoolExecutor` to read the pipe. Reading from
      the pipe in a different OS thread seems like overkill, but I have not
      profiled it.
   2. Run the cognitive engine using the `asyncio` event loop's
      `run_in_executor`
      method with a `concurrent.futures.ProcessPoolExecutor`. This does not seem
      like a good option because we can only get results when the function
      passed to `run_in_executor` returns. Using this method without restarting
      the cognitive engine each time we want to process a new frame would
      probably require a hacky solution that `run_in_executor` was not intended
      for. Therefore, this seems like a bad option.
   3. You can start a subprocess by calling a python script with
      `asyncio.create_subprocess_exec`. Unfortunately you can only communicate
      with these subprocesses using stdin/stoud or file descriptors that you
      leave open with the `close_fds` or `pass_fds` arguments. However, we need
      to use `multiprocessing.queue()` for our inputs to the cognitive engine.
      Using `os.pipe()` or `multiprocessing.pipe()` is not an option because
      these might get full and block the event loop. Changing file descriptors
      to non-blocking mode will not work because some individual input frames
      might be very large. Pipe size can be increased, but there is a limit to
      this. It's better to use `multiprocessing.queue()`, which will make a best
      effort attempt to hold the number of items we specify when we instantiate
      it. Unless there is some way to pass a `multiprocessing.queue()` to a
      subprocess created with `asyncio.create_subprocess_exec` that isn't some
      horrible hack, you should not start the cognitive engine process with
      `asyncio.create_subprocess_exec`.
   4. Future versions of Python might offer a high level interface for
      interprocess communication that does not block the `asyncio` event loop.
      This might be a good option for sending results from the cognitive engine
      to the websocket server. Note that sending results in the other direction
      (from the websocket server to the cognitive engine) should be done using
      a queue that will not get full (such as `multiprocessing.queue()`).
6. The security of Gabriel could be improved in a number of areas. The
   connections between clients and the server, and the connections between
   the `network_engine.server_runner` and engine runners, could both be
   improved. These improvements could be in the form of encrypting traffic,
   requiring a password for clients and engine runners to connect to the server,
   and specifying a list of approved clients and engine runners on the server.

## Publishing Changes to PyPi

Update the version number in setup.py. Then follow
[these instructions](https://packaging.python.org/tutorials/packaging-projects/#generating-distribution-archives).
