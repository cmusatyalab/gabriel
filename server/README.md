# Gabriel Server Library

## Installation

Requires Python 3.6 or newer.

Run `pip install gabriel-server`

## Usage

Data is processed by Cognitive Engines. Each cognitive engine is implemented in
a separate class that inherits `cognitive_engine.Engine`. The `handle` method is
called each time there is a new frame for the engine to process. `handle` gets
passed an
[`InputFrame`](https://github.com/cmusatyalab/gabriel/blob/2840808c3d90e4980969b2744877e739723c84bb/protocol/gabriel.proto#L20).
It must return a
[`ResultWrapper`](https://github.com/cmusatyalab/gabriel/blob/2840808c3d90e4980969b2744877e739723c84bb/protocol/gabriel.proto#L33).
The `handle` method should create a `ResultWrapper` using the
`cognitive_engine.create_result_wrapper` function. The `handle` method can add
results to this `ResultWrapper`, or just return the `ResultWrapper` instance it
gets from `create_result_wrapper` without adding results (if the client does not
expect results back). The client will get a token back as soon as `handle`
returns a `ResultWrapper` (even if the `ResultWrapper` instance just came from
`create_result_wrapper` and nothing else wass added to it). Therefore, returning
from `handle` before the engine is ready for the next frame will cause the
engine to get saturated with requests faster than they can be processed.

### Single Engine Workflows

The simplest possible setup involves a single cognitive engine. In this case,
the Gabriel Server and the cognitive engine are run in the same Python program.
Start the engine and server as follows:

```python
local_engine.run(engine_factory=lambda: MyEngine(), source_name='my_source',
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
engine_runner.run(engine=MyEngine(), source_name='my_source',
                  server_address='tcp://localhost:5555',
		  all_responses_required=True)
```

Note that `engine` should be a reference to an existing engine, not a function
that runs the constructor for the engine. Unlike `local_engine`,
`network_engine.engine_runner` does not run the engine in a separate process.

When `all_responses_required` is False, the client will not receive a result
from this engine, if a different engine processing the same frame already
returned a result for this frame. When `all_responses_required` is True,
the server will send every result this engine returns. Typically, you should set
`all_responses_required` to True when an engine returns results to the clients,
and False when an engine stores results but does not include anything useful for
the client in the `ResultWrapper` instance that it returns.

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
re-establish a lost connection with the Gabriel server. The number of retry
attempts do not get replenished at any point during the engine runner's
execution. The default `timeout` and `request_retries` values should be
sufficient for most configurations.

## Publishing Changes to PyPi

Update the version number in setup.py. Then follow
[these instructions](https://packaging.python.org/tutorials/packaging-projects/#generating-distribution-archives).
