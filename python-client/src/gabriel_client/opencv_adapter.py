import cv2
import numpy as np
from gabriel_protocol import gabriel_pb2
from gabriel_client.websocket_client import ProducerWrapper
import logging


logger = logging.getLogger(__name__)


class OpencvAdapter:
    def __init__(
        self, preprocess, produce_extras, consume_frame, video_capture, source_name
    ):
        """
        preprocess should take one frame parameter
        produce_engine_fields takes no parameters
        consume_frame should take one frame parameter and one engine_fields
        parameter
        """

        self._preprocess = preprocess
        self._produce_extras = produce_extras
        self._consume_frame = consume_frame
        self._video_capture = video_capture
        self._source_name = source_name

    def get_producer_wrappers(self):
        async def producer():
            _, frame = self._video_capture.read()
            if frame is None:
                return None

            frame = self._preprocess(frame)
            _, jpeg_frame = cv2.imencode(".jpg", frame)

            input_frame = gabriel_pb2.InputFrame()
            input_frame.payload_type = gabriel_pb2.PayloadType.IMAGE
            input_frame.payloads.append(jpeg_frame.tobytes())

            extras = self._produce_extras()
            if extras is not None:
                input_frame.extras.Pack(extras)

            return input_frame

        return [ProducerWrapper(producer=producer, source_name=self._source_name)]

    def consumer(self, result_wrapper):
        if len(result_wrapper.results) != 1:
            logger.error("Got %d results from server", len(result_wrapper.results))
            return

        result = result_wrapper.results[0]
        if result.payload_type != gabriel_pb2.PayloadType.IMAGE:
            type_name = gabriel_pb2.PayloadType.Name(result.payload_type)
            logger.error("Got result of type %s", type_name)
            return

        np_data = np.frombuffer(result.payload, dtype=np.uint8)
        frame = cv2.imdecode(np_data, cv2.IMREAD_COLOR)

        self._consume_frame(frame, result_wrapper.extras)
