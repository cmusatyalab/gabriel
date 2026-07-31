"""A Websocket client that measures performance metrics."""

import logging
import time
from typing import Callable

from gabriel_protocol import gabriel_pb2

from gabriel_client.gabriel_client import InputProducer
from gabriel_client.websocket_client import WebsocketClient

logger = logging.getLogger(__name__)


class MeasurementClient(WebsocketClient):
    """A WebSocket client that measures performance metrics."""

    def __init__(
        self,
        server_endpoint: str,
        input_producers: list[InputProducer],
        consumer: Callable[[gabriel_pb2.Result], None],
        output_freq: int = 10,
        **kwargs,
    ):
        """Initialize the measurement client."""
        super().__init__(server_endpoint, input_producers, consumer, **kwargs)

        self._output_freq = output_freq
        self._start_time = None
        self._source_measurements = {}

    def _process_registered(self, registered):
        super()._process_registered(registered)
        self._start_time = time.time()

    def _get_source_measurement(self, producer_id):
        source_measurement = self._source_measurements.get(producer_id)
        if source_measurement is None:
            source_measurement = _SourceMeasurement(
                self._start_time, self._output_freq
            )
            self._source_measurements[producer_id] = source_measurement
        return source_measurement

    def _process_response(self, result_wrapper):
        response_time = time.time()
        super()._process_response(result_wrapper)
        if result_wrapper.return_token:
            source_measurement = self._get_source_measurement(
                result_wrapper.producer_id
            )
            source_measurement.process_response(
                result_wrapper.result.frame_id,
                result_wrapper.producer_id,
                response_time,
            )

    async def _send_from_client(self, from_client):
        await super()._send_from_client(from_client)
        send_time = time.time()
        source_measurement = self._get_source_measurement(
            from_client.input.producer_id
        )
        source_measurement.log_send(from_client.input.frame_id, send_time)


class _SourceMeasurement:
    def __init__(self, start_time, output_freq):
        self._count = 0
        self._send_timestamps = {}
        self._recv_timestamps = {}
        self._start_time = start_time
        self._interval_start_time = start_time
        self._output_freq = output_freq

    def process_response(self, frame_id, producer_id, response_time):
        self._recv_timestamps[frame_id] = response_time
        self._count += 1

        if (self._count % self._output_freq) == 0:
            self._compute_and_print(producer_id, response_time)
            self._interval_start_time = time.time()

    def _compute_and_print(self, producer_id, response_time):
        print("Measurements for producer:", producer_id)
        overall_fps = _compute_fps(
            self._count, response_time, self._start_time
        )
        print("Overall FPS:", overall_fps)
        interval_fps = _compute_fps(
            self._output_freq, response_time, self._interval_start_time
        )
        print("Interval FPS:", interval_fps)

        total_rtt = 0
        for frame_id, received in self._recv_timestamps.items():
            sent = self._send_timestamps[frame_id]
            total_rtt += received - sent
            del self._send_timestamps[frame_id]

        print("Average RTT for interval:", total_rtt / self._output_freq)
        self._recv_timestamps.clear()

    def log_send(self, frame_id, send_time):
        self._send_timestamps[frame_id] = send_time


def _compute_fps(num_frames, current_time, start_time):
    return num_frames / (current_time - start_time)
