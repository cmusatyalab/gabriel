# H.264 engines

Two Gabriel engines that send video frames over a slow link as H.264 instead of raw pixels:

| Engine        | Takes                                   | Emits                                    |
| ------------- | --------------------------------------- | ---------------------------------------- |
| `h264-encode` | a raw RGB frame (SteelEagle `RawFrame`) | an `EncodedVideoFrame`                   |
| `h264-decode` | an `EncodedVideoFrame`                  | the raw RGB frame, of the original type  |

A typical flow runs the encoder on the device and the decoder on the edge server, before a
detector:

```
camera → yolo → h264-encode ══ crossing ══► h264-decode → detector
```

## Keyframe on gap

Gabriel keeps results fresh by skipping stale frames, and a crossing may time a frame out. But
an H.264 P-frame only decodes after every frame before it, back to a keyframe. So:

- The encoder numbers the frames it encodes. After a gap in the numbers, the decoder drops
  frames until the next keyframe, and its result says it needs one.
- The encoder subscribes to the decoder's events. When a frame it encoded since its last
  keyframe is skipped, times out, or is dropped by the decoder, it makes its next frame a
  keyframe. A skip at the crossing is known on the device at once, so the keyframe usually
  comes on the very next frame.

## Frames and their fields

The engines read only a frame's pixels and size: the fields of `RawFrameView` in
`proto/h264.proto`, which have the same numbers as in SteelEagle's `RawFrame`. The other fields,
such as the capture timestamp and the position, pass through unchanged, and the decoder emits the
frame with the payload type the encoder got. So the engines work with either SteelEagle package
(`messages.stream` or `messages.telemetry`).

## Building

On the machine that runs them, with GStreamer's development files installed:

```sh
cargo build --release -p gabriel-h264
```

For a Particle Tachyon (aarch64, Ubuntu 20.04), cross-compile from the repository root:

```sh
docker build -t gabriel-h264-aarch64 -f examples/h264/Dockerfile.aarch64 examples/h264
docker run --rm -u $(id -u):$(id -g) -v $PWD:/src -w /src -e CARGO_TARGET_DIR=/src/target/aarch64 \
    gabriel-h264-aarch64 cargo build --release --target aarch64-unknown-linux-gnu -p gabriel-h264
```

## Running

Each engine waits for a Gabriel server to serve it. The encoder and decoder elements are
GStreamer elements, in `gst-launch-1.0` syntax:

```sh
# On a Tachyon, with its hardware encoder
h264-encode --encoder "qtic2venc control-rate=2 target-bitrate=3000000 b-frames=0"
# Elsewhere, e.g. with x264 (the default) or OpenH264
h264-encode --encoder "openh264enc bitrate=3000000"

h264-decode                                  # libav, on the CPU
h264-decode --decoder nvh264dec              # NVDEC
```

`--log-frames` prints a line per frame: its number, whether it is a keyframe, its size, and how
long it took. `--help` lists the rest.

The encoder must not hold frames back: each frame's output has to come out before the next frame
goes in. Turn off B-frames and lookahead, as the defaults do. A frame whose output does not come
within `--timeout-ms` is discarded.
