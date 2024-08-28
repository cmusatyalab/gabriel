import asyncio
import logging

logger = logging.getLogger(__name__)

class _Source:
    def __init__(self, num_tokens):
        self._num_tokens = num_tokens
        self._sem = asyncio.Semaphore(num_tokens)
        self._frame_id = 0

    def return_token(self):
        self._sem.release()

    async def get_token(self):
        logger.debug('Waiting for token')
        await self._sem.acquire()

        self._num_tokens -= 1

    def is_locked(self):
        return self._sem.locked()

    def get_frame_id(self):
        return self._frame_id

    def next_frame(self):
        self._frame_id += 1
