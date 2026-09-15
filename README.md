# Gabriel

Gabriel is a framework for wearable cognitive assistance using cloudlets.
You can find more details about Gabriel from our [design document](design), our
[paper](http://dl.acm.org/citation.cfm?id=2594383), and our
[website](http://gabriel.cs.cmu.edu).

## Getting Started

1. Create a Gabriel [server](server).
2. Create a client using the [Python](python-client), [Go](go-client), or
   [Android](android-client) client library.
3. Write a cognitive engine that connects to the server and processes frames
   from a client.

Clients, engines, and the server communicate over gRPC (the default), or
optionally WebSocket or ZeroMQ, using the protobuf messages defined in
[protocol](protocol). Connections can be secured with TLS.

## Example Workflows

1. [OpenRTiST](https://github.com/cmusatyalab/openrtist)
2. [Instruction-based assistants](https://github.com/cmusatyalab/gabriel-instruction)

The [examples](examples) directory of this repository contains some toy
workflows, and [tests/integration](tests/integration) has end-to-end tests
that double as usage examples.

## Details

The following section provides low-level details about how this code works. See
our [design document](design) for a higher-level explanation.

Clients send one frame to the server at a time. Each frame comes from a
producer, identified by a `producer_id` (such as "openrtist" or "face"). A
producer can be an interactive application that sends frames without
filtering them (such as OpenRTiST), or an early discard filter. Two different
early discard filters can send frames captured by the same sensor, but they
are still different producers from Gabriel's perspective.

Every frame from one producer should have the same `PayloadType`, and this
type should not change. For example, if a producer sends images, it should
only ever send images, not also audio. Each `FromClient.Input` message
explicitly lists the `target_engine_ids` it should be routed to, so a client
decides at send time which cognitive engines see a given frame.

Each client has one set of tokens per producer. This allows the client to
send frames from "producer x" at a different rate than it sends frames from
"producer y." Multiple cognitive engines can consume frames from the same
producer.

The Gabriel server returns a token to the client for "producer x" as soon as
the first cognitive engine targeted by a frame from "producer x" returns a
result for that frame. When a second targeted engine returns a result for the
same frame, the server does not return a second token. An engine can register
with `all_responses_required` set so the server always forwards its results
to the client, even when it isn't the first engine to respond; in that case
the server still only returns one token per frame.

Cognitive engines might not receive every frame sent to the server. In
particular, the client will send frames to the server at the rate that the
fastest targeted engine can process them. Slower engines might miss frames
that were given to the fastest one. After an engine finishes processing its
current frame, it is given the most recent frame available for it, not
necessarily the next one in the queue.

### Flow Control

Gabriel's flow control is based on tokens. When the client sends a frame to the
server, this consumes a token for the producer that produced the frame. When
the first targeted cognitive engine finishes processing this frame, the client
gets back the token that was consumed sending the frame. This ensures that
frames are sent to the server at the rate that the fastest targeted engine
can process them. If the server runs into an error processing a frame, it
immediately sends a message to the client indicating the return of a token.

After a client consumes all of its tokens for a producer, the client will only
send a new frame from this producer after it receives a token back
(for this producer). This can lead to periods where the server has no input when
the latency between clients and the server is high. Setting a high number of
tokens will fill up the queue of inputs on the server and thus reduce the length
of these idle periods. However, the frames in the queue might be stale by the
time they get processed. You should not set the number of tokens above two,
unless the latency between clients and the server is high, and your workload is
not latency critical.

Each `FromClient.Input` message the client sends consumes one token. A
`ToClient.ResultWrapper` message with `return_token` set to true indicates
the return of one token. Specifying the specific number of tokens that a
client has for a producer in the `ResultWrapper` message would lead to race
conditions based on the order that the client and server send and receive
messages. Representing the consumption or return of a single token in a
message avoids this problem. Clients communicate with the server over gRPC
by default (WebSocket and ZeroMQ transports are also available), all of
which run over TCP, so we assume that messages are delivered reliably and in
order.

## Future Improvements

1. If two sources both send the same payload, the payload will be sent to the
   server twice. Caching payloads, and referencing the cached item in subsequent
   `FromClient` messages would save bandwidth.
2. We allow multiple different cognitive engines to consume frames from the
   same source. However, there is no way to have
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
      and engine can pass state back and forth to each other in the `extras`
      field of `InputFrame` and `ResultWrapper` messages. This would allow the
      client's frames to be processed by any instance of a given engine.
      However, your client code needs to ignore results based on frames that the
      client sent before it received the latest state update.
   2. Each client is assigned to a specific instance of an engine. No other
      instances of this engine will get frames from this client. This setting
      will be used for engines that store state information for each client.
3. The security of Gabriel could be improved further. Connections between
   clients and the server, and between the server and standalone engine
   runners, can already be encrypted with TLS (including mutual TLS via a
   client CA cert). However, Gabriel still does not support requiring a
   password or token for clients and engine runners to connect to the
   server, nor specifying a list of approved clients and engine runners in a
   server configuration file.
