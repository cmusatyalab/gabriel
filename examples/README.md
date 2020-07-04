# Gabriel Examples

These examples show simple Gabriel use cases. You must install all dependencies
listed in `requirements.txt`. I recommend using a
[virtual environment](https://packaging.python.org/guides/installing-using-pip-and-virtual-environments/).

The clients need to be run on a computer with a webcam. The server and engines
can be run on a computer without a webcam. The following instructions provide
commands that assume the client, server, and engine(s) are all running on the
same computer
(unless otherwise stated). If you would like to run these examples with a
client or engine running on a different computer then the server is running on,
specify the `server_host` command-line flag when starting the client or engine.

## Round Trip

The round trip client captures a frame from your webcam, sends it to the server,
the server returns the frame to the client, and then the client displays the
frame.

The round trip server uses Gabriel's single engine workflow.

Start the server by running `python3 round_trip/server.py` in one terminal, and
then run `python3 round_trip/client.py` in a second terminal.

## One Way

The one way clients capture a frame, send it to the server, but then the server
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

To run two producers and see their outputs, stop the producer client. Then run:

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
