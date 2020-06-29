from gabriel_client.websocket_client import WebsocketClient
from gabriel_client.websocket_client import ProducerWrapper
import time
import logging


logger = logging.getLogger(__name__)


class TimingClient(WebsocketClient):
    def __init__(self, host, port, producer_wrappers, consumer, output_freq=10):
        super().__init__(host, port, producer_wrappers, consumer)

        self._source_timings = {}
        self._output_freq = output_freq

    def _process_welcome(self, welcome):
        super()._process_welcome(welcome)
        start_time = time.time()
        for source_name in welcome.sources_consumed:
            source_timing = _SourceTiming(start_time, self._output_freq)
            self._source_timings[source_name] = source_timing

    def _process_response(self, response):
        response_time = time.time()
        super()._process_response(response)
        if response.return_token:
            source_timing = self._source_timings[response.source_name]
            source_timing.process_response(response.frame_id, response_time)

    async def _send_from_client(self, from_client):
        await super()._send_from_client(from_client)
        send_time = time.time()
        source_timing = self._source_timings[from_client.source_name]
        source_timing.log_send(from_client.frame_id, send_time)

class _SourceTiming:
    def __init__(self, start_time, output_freq):
        self._count = 0
        self._interval_count = 0
        self._send_timestamps = {}
        self._recv_timestamps = {}
        self._start_time = start_time
        self._interval_start_time = start_time
        self._output_freq = output_freq

    def process_response(self, frame_id, response_time):
        self._recv_timestamps[frame_id] = response_time
        self._interval_count += 1

        if self._interval_count % self._output_freq == 0:
            self._count += self._interval_count
            self._compute_and_print(response_time)
            self._interval_count = 0
            self._interval_start_time = time.time()

    def _compute_and_print(self, response_time):
        overall_fps = self._count / (response_time - self._start_time)
        print('Overall FPS:', overall_fps)
        interval_fps = (self._interval_count /
                            (response_time - self._interval_start_time))
        print('Interval FPS:', interval_fps)

        total_rtt = 0
        for frame_id, received in self._recv_timestamps.items():
            sent = self._send_timestamps[frame_id]
            total_rtt += (received - sent)
            del self._send_timestamps[frame_id]

        print('Average RTT for interval:', total_rtt / self._output_freq)
        self._recv_timestamps.clear()

    def log_send(self, frame_id, send_time):
        self._send_timestamps[frame_id] = send_time
