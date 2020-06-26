from gabriel_client.server_comm import WebsocketClient
from gabriel_client.server_comm import ProducerWrapper
import time
import logging


logger = logging.getLogger(__name__)


class TimingClient(WebsocketClient):
    def __init__(self, host, port, producer_wrappers, consumer, output_freq=10):
        super().__init__(host, port, None, self.consumer)
        self._adapter_consumer = consumer

        self._output_freq = output_freq
        self._send_timestamps = {}
        self._recv_timestamps = {}

        self._count = 0
        self._interval_count = 0
        self._start_time = time.time()
        self._interval_start_time = time.time()

        self.producer_wrappers = [
            self._producer_wrapper(producer_wrapper)
            for producer_wrapper in producer_wrappers
        ]

    def consumer(self, result_wrapper):
        self._adapter_consumer(result_wrapper)

        timestamp = time.time()
        self._recv_timestamps[result_wrapper.frame_id] = timestamp
        self._count += 1
        self._interval_count += 1

        if self._count % self._output_freq == 0:
            overall_fps = self._count / (timestamp - self._start_time)
            print('Overall FPS:', overall_fps)
            interval_fps = (self._interval_count /
                            (timestamp - self._interval_start_time))
            print('Interval FPS:', interval_fps)

            self._interval_count = 0
            self._interval_start_time = time.time()

    def _producer_wrapper(self, producer_wrapper):
        async def producer_with_timing():
            from_client = await producer_wrapper.producer()
            if from_client is not None:
                self._send_timestamps[self.get_frame_id()] = time.time()

            return from_client

        return ProducerWrapper(
            producer=producer_with_timing,
            filter_name=producer_wrapper.filter_name)

    def compute_avg_rtt(self):
        count = 0
        total_rtt = 0

        for frame_id, sent in self._send_timestamps.items():
            received = self._recv_timestamps.get(frame_id)
            if received is None:
                logger.error('Frame with ID %d never received', frame_id)
            else:
                count += 1
                total_rtt += (received - sent)

        print('Average RTT', total_rtt / count)

    def clear_timestamps(self):
        self._send_timestamps.clear()
        self._recv_timestamps.clear()
