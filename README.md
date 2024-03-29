# Gabriel

Gabriel is a framework for wearable cognitive assistance using cloudlets.
You can find more details about Gabriel from our [design document](design), our
[paper](http://dl.acm.org/citation.cfm?id=2594383), and our
[website](http://gabriel.cs.cmu.edu).

## Getting Started

1. Create a Gabriel [server](server).
2. Create a client for [Python](python-client) or [Android](android-client).

## Example Workflows

1. [OpenRTiST](https://github.com/cmusatyalab/openrtist)
2. [Instruction-based assistants](https://github.com/cmusatyalab/gabriel-instruction)

The [examples](examples) directory of this repository contains some toy
workflows.

## Details

The following section provides low-level details about how this code works. See
our [design document](design) for a higher-level explanation.

Clients send one frame to the server at a time. Clients have sources, which
produce frames. A source can be an interactive application that sends frames
without filtering them (such as OpenRTiST), or an early discard filter. Two
different early discard filters can send frames that were captured by the same
sensor. However, these filters are different sources from Gabriel's perspective.
Each source must be given a name (such as "openrtist" or "face"). This allows
cognitive engines to know what to expect in input frames and (if applicable)
what results the client expects back.

Every frame from one source should have the same type of data, and this type
should not change. For example, if a source sends images, it should only ever
send images, and it should not also include audio along with an image. Audio and
images should be sent by two different sources. `InputFrame` messages have an
`extras` field that can be used to send metadata, such as GPS and IMU
measurements, or app state. Embedding binary data as `extras` to circumvent the
"one type of media per source" restriction will likely lead to cognitive
engines that are difficult for other people to maintain. Multiple payloads can
be sent in a single `InputFrame` message. This is intended for cases where an
input to an engine must contain several consecutive images. A single
`InputFrame` message should represent one single input to a cognitive engine.

Each client has one set of tokens per source. This allows the client to send
frames that have passed "source x" at a different rate than it sends frames that
have passed "source y." A cognitive engine can only consume frames that have
passed one source. A cognitive engine cannot change the source that it consumes
frames from. Multiple cognitive engines can consume frames that pass the same
source.

The Gabriel server returns a token to the client for "source x" as soon as the
first cognitive engine that consumes frames from "source x" returns a
`ResultWrapper` for that frame. When a second cognitive engine that also
consumes frames from "source x" returns a `ResultWrapper` for the same frame,
the Gabriel server does not return a second token to the client. Engines can be
configured to allow the server to ignore a `ResultWrapper` if this engine was
not the first to return a `ResultWrapper` for a frame. Engines can also be
configured to force the server to send all `ResultWrapper` messages to the
client.
When an engine configured to require all responses is not the first engine to
return a `ResultWrapper` for a frame, the server will send the client this
`ResultWrapper`, but it will not return a token (because it already returned the
token for this frame with the result from the first cognitive engine).

Cognitive engines might not receive every frame sent to the server. In
particular, the client will send frames to the server at the rate that the
fastest cognitive engine can process them. Slower engines that consume frames
from the same source might miss some of the frames that were given to the
fastest engine for this source. After an engine finishes processing its current
frame, it will be given the most recent frame that was given to the fastest
engine. When the first engine completes the most recent frame, a new frame will
be taken off the input queue and given to this engine.

### Flow Control

Gabriel's flow control is based on tokens. When the client sends a frame to the
server, this consumes a token for the source that produced the frame. When the
first cognitive engine finishes processing this frame, the client gets back the
token that was consumed sending the frame. This ensures that frames are sent to
the server at the rate that the fastest engine consuming frames from this source
can process them. If the server runs into an error processing a frame, it
immediately sends a message to the client indicating the return of a token.

After a client consumes all of its tokens for a source, the client will only
send a new frame from this source after it receives a token back
(for this source). This can lead to periods where the server has no input when
the latency between clients and the server is high. Setting a high number of
tokens will fill up the queue of inputs on the server and thus reduce the length
of these idle periods. However, the frames in the queue might be stale by the
time they get processed. You should not set the number of tokens above two,
unless the latency between clients and the server is high, and your workload is
not latency critical.

Each `FromClient` message the client sends consumes one token. A
`ToClient.Response` message with `return_token` set to true indicates the return
of one token. Specifying the specific number of tokens that a client has for a
source in the `ToClient.Response` message would lead to race conditions based on
the order that the client and server send and receive messages. Representing the
consumption or return of a single token in a message avoids this problem.
Clients communicate with the server using
[The WebSocket Protocol](https://tools.ietf.org/html/rfc6455), which uses TCP.
Therefore, we assume that messages are delivered reliably and in order.

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
3. Gabriel does not expose any client identification information to cognitive
   engines. Clients can include this information in the `extras` field of
   `InputFrame` messages. However, this should be added as a part of Gabriel
   itself at some point.
   1. Should this identity persist when the client disconnects and reconnects?
   2. If support for multiple instances of the same engine is added, should this
      identity be used when a group is set to assign a client to one specific
      instance of an engine?
4. When cognitive engines are run separately from the server, clients do
   not handle the case when all engines that consume a source disconnect. To
   handle this case, the Gabriel server would need to tell clients when this
   happens. The clients would then need to stop sending inputs from this source.
5. The security of Gabriel could be improved in a number of areas. The
   connections between clients and the server, and the connections between
   the server and standalone engine runners, could both be improved. These
   improvements could be in the form of encrypting traffic, requiring a password
   for clients and engine runners to connect to the server, and specifying a
   list of approved clients and engine runners in a server configuration file.
