# /// script
# requires-python = ">=3.11"
# dependencies = ["grpcio-tools", "pillow"]
# ///
"""Prepares camera frames for the bench to replay: each image, resized to one
resolution, as a serialized SteelEagle RawFrame (packed 8-bit RGB) and as a
JPEG of the same pixels. Replaying both shows what decoding costs an engine,
apart from resolution.

    uv run bench/raw_frames.py --proto-path <dir with steeleagle_protocol/> \\
        --out frames-720p frames/*.png

writes frames-720p/raw/*.pb and frames-720p/jpeg/*.jpg. A run replays the
RawFrames with:

    [producers.camera]
    frames = "frames-720p/raw"
    input_type = "steeleagle_protocol.v1.messages.telemetry.RawFrame"
"""

import argparse
import importlib
import sys
import tempfile
from pathlib import Path

from grpc_tools import protoc
from PIL import Image, ImageOps

TELEMETRY_PROTO = "steeleagle_protocol/v1/messages/telemetry/telemetry.proto"
COMMON_PROTO = "steeleagle_protocol/v1/common/common.proto"
TELEMETRY_MODULE = "steeleagle_protocol.v1.messages.telemetry.telemetry_pb2"
RGB_CHANNELS = 3
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1280, 720
# OpenCV's default JPEG quality, as a camera app using it would encode.
JPEG_QUALITY = 95


def load_telemetry_messages(proto_path: Path, out: Path):
    """Compiles the SteelEagle telemetry messages into `out`, and imports them."""
    # protoc's own include directory has the well-known types, e.g.
    # google/protobuf/timestamp.proto.
    well_known = Path(protoc.__file__).parent / "_proto"
    status = protoc.main([
        "protoc",
        f"-I{proto_path}",
        f"-I{well_known}",
        f"--python_out={out}",
        TELEMETRY_PROTO,
        COMMON_PROTO,
    ])
    if status != 0:
        sys.exit(f"protoc failed to compile {TELEMETRY_PROTO} from {proto_path}")
    sys.path.insert(0, str(out))
    return importlib.import_module(TELEMETRY_MODULE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--proto-path", type=Path, required=True,
                        help="the directory that holds steeleagle_protocol/")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args()

    raw_dir, jpeg_dir = args.out / "raw", args.out / "jpeg"
    raw_dir.mkdir(parents=True, exist_ok=True)
    jpeg_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as generated:
        telemetry = load_telemetry_messages(args.proto_path, Path(generated))
        size = (args.width, args.height)
        for path in sorted(args.images):
            # Crops to the aspect ratio if it differs, rather than stretching.
            image = ImageOps.fit(Image.open(path).convert("RGB"), size, Image.Resampling.LANCZOS)
            frame = telemetry.RawFrame(
                data=image.tobytes(),
                h_res=args.width,
                v_res=args.height,
                channels=RGB_CHANNELS,
            )
            (raw_dir / f"{path.stem}.pb").write_bytes(frame.SerializeToString())
            image.save(jpeg_dir / f"{path.stem}.jpg", quality=JPEG_QUALITY)
            print(f"{path.name}: {args.width}x{args.height}")


if __name__ == "__main__":
    main()
