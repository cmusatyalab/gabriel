"""Adapter to integrate OpenCV with Gabriel client framework."""

import logging

import cv2
import numpy as np
from gabriel_protocol import gabriel_pb2

from gabriel_client.gabriel_client import InputProducer

logger = logging.getLogger(__name__)


class OpencvAdapter:
    """Adapter to integrate OpenCV with the Gabriel client framework."""

    def __init__(
        self,
        preprocess,
        consume_frame,
        video_capture,
        engine_name,
    ):
        """Initialize the adapter.

        Args:
            preprocess: A function to preprocess the video frame.
            consume_frame:
                A function to consume the output frame from the server.
            video_capture: The OpenCV video capture object.
            engine_name: The name of the video processing engine.

        """
        self._preprocess = preprocess
        self._consume_frame = consume_frame
        self._video_capture = video_capture
        self._engine_name = engine_name

    def get_producer_wrappers(self):
        """Get the producer wrappers for the video source."""

        async def producer():
            _, frame = self._video_capture.read()
            if frame is None:
                return None

            frame = self._preprocess(frame)
            _, jpeg_frame = cv2.imencode(".jpg", frame)

            input_frame = gabriel_pb2.InputFrame()
            input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
            input_frame.byte_payload = jpeg_frame.tobytes()

            return input_frame

        return [
            InputProducer(
                producer=producer, target_engine_ids=[self._engine_name]
            )
        ]

    def consumer(self, result):
        """Consume the output frame from the server."""
        assert result.WhichOneof("payload") == "bytes_result"
        np_data = np.frombuffer(result.bytes_result, dtype=np.uint8)
        frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)

        self._consume_frame(frame)
