# Gabriel Examples

These examples show simple Gabriel use cases. You must install all dependencies
listed in `requirements.txt`. I recommend using a
[virtual environment](https://packaging.python.org/guides/installing-using-pip-and-virtual-environments/).

The Python clients need to be run on a computer with a webcam. The server and
engines can be run on a computer without a webcam. The instructions in this
README provide commands that assume the Python client, server, and engine(s) are
all running on the same computer (unless otherwise stated). If you would like to
run these examples with a Python client or engine running on a different
computer than the server is running on, specify the `server_host` command-line
flag when starting the client or engine.

All examples have Python clients. Only the Round Trip and One Way YUV examples
have Android clients. You need to specify the server host for the Android
clients by adding the line `gabrielHost="<THE_HOST>"` to
`android-client/local.properties` for the client you are trying to run.

The extra `ToServer` protobuf fields for the One Way YUV example are specified
in `one_way_yuv/android-client/app/src/main/proto/yuv.proto`. If you modify
these fields, the proto will be rebuilt for Android automatically, the next time
you build the App with Android Studio. You must recompile the proto for Python
by running:

```bash
/path/to/protoc --python_out one_way_yuv --proto_path one_way_yuv/android-client/app/src/main/proto yuv.proto
```

## Round Trip

The round trip client captures a frame from your webcam, sends it to the server,
the server returns the frame to the client, and then the client displays the
frame.

The round trip server uses Gabriel's single engine workflow.

Start the server by running `python3 round_trip/server.py` in one terminal, and
then run `python3 round_trip/client.py` in a second terminal.

## One Way

The one way clients capture a frame, sends it to the server, then the server
sends frames to cognitive engines that display the frames. The server replies
to the clients to return tokens, but these replies do not contain any results.

The producer client adds text to images, indicating its own number.

Run the following commands in separate terminals:

```bash
python3 one_way/server.py
```

```bash
python3 one_way/engine.py
```

```bash
python3 one_way/producer_client.py
```

To run two producers and see their outputs, stop the producer client (but keep
the server and engine running). Then run:

```bash
python3 one_way/engine.py 1
```

```bash
python3 one_way/producer_client.py 2
```

You should now see windows displaying frames from both of the engines that are
running. You might need to move one window out of the way, if both windows start
out stacked on top of each other.

You can start a second engine that also consumes frames from producer `0` by
running:


```bash
python3 one_way/engine.py 0
```

Lastly, you can view frames generated from another computer while everything you
have already started continues to run. First run `python3 one_way/engine.py 2`
in a separate terminal on the computer that is running the server. Then run
`python3 one_way/push_client.py 2 [IP of server]` from a different computer.

## One Way YUV

The One Way YUV client uses Gabriel's single engine workflow. Frames are sent
to the engine using YUV NV21 encoding. This saves the smartphone from having to
do JPEG encoding, and likely avoids compression artifacts. However, it requires
transmitting 1.5 bytes for every pixel in the image.

The image dimensions and rotation of the image are added to the extras field of
the `InputFrame` proto.

## Empty Messages

The empty messages example sends `InputFrame` protos to the server, without
adding anything to them. This example functions as a ping test.

This example doesn't have a real sensor slowing down the rate that new inputs
are produced, so it's important to keep the call to `asyncio.sleep` in the
producer. Otherwise the client will send input to the server as fast as it can.
