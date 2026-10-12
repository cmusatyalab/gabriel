# Object detection with YOLO26

An app sends video frames through Gabriel to a YOLO26 engine, and prints what it finds in each.
Everything runs on this machine. Frames go through shared memory as raw images, with no copy or
encoding on the way.

| File          | Process                                                         |
| ------------- | --------------------------------------------------------------- |
| `server.py`   | The local server. It delivers the app's frames to the detector. |
| `detector.py` | The engine: runs YOLO26 on each frame, and returns JSON.        |
| `app.py`      | The app: reads a video, sends its frames, prints the results.   |
| `common.py`   | Names and the frame size that all three share.                  |

## Running it

```sh
cargo build --release -p gabriel              # from the repository root
pip install -r requirements.txt               # from this directory
```

Then, each in its own terminal in this directory:

```sh
python server.py
python detector.py                            # downloads yolo26n.pt the first time
python app.py                                 # or: python app.py --source video.mp4
```

The app prints one line per result:

```text
frame     2:   7.2 ms old, 5 objects: person 88%, bus 87%, person 86%, person 82%, person 79%
```

"ms old" is the time from when the app published the frame to when its result arrived. Stop each
process with Ctrl-C.
