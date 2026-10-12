"""Names and frame size that the server, the detector and the app share."""

SERVER = "yolo-server"
ENGINE = "yolo"
PRODUCER = "yolo-camera"
FLOW = "detection"

# Frames are raw 8-bit BGR images of this size, as OpenCV reads them.
WIDTH, HEIGHT, CHANNELS = 640, 480, 3
FRAME_SIZE = WIDTH * HEIGHT * CHANNELS
