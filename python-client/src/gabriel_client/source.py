import asyncio
import logging

logger = logging.getLogger(__name__)

class _Source:
    def __init__(self, num_tokens):
        self._num_tokens = num_tokens
        self._event = asyncio.Event()
        self._frame_id = 0

    def return_token(self):
        self._num_tokens += 1
        self._event.set()

    async def get_token(self):
        while self._num_tokens < 1:
            logger.debug('Waiting for token')
            self._event.clear()  # Clear because we definitely want to wait
            await self._event.wait()

        self._num_tokens -= 1

    def get_num_tokens(self):
        return self._num_tokens

    def get_frame_id(self):
        return self._frame_id

    def next_frame(self):
        self._frame_id += 1
