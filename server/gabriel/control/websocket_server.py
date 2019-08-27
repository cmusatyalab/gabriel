import asyncio
import websockets
from gabriel.control import gabriel_pb2
import os
import gabriel
import time
import traceback
import json

# Show exceptions from websockets server
import logging
logger = logging.getLogger('websockets.server')
logger.setLevel(logging.ERROR)
logger.addHandler(logging.StreamHandler())

LOG = gabriel.logging.getLogger(__name__)


image_queue_list = [
    asyncio.Queue(maxsize=gabriel.Const.MAX_FRAME_SIZE)
]
result_queue = asyncio.Queue()


class HandlerState:
    def __init__(self, start_time):
        if gabriel.Debug.LOG_STAT:
            self.frame_count = 0
            self.total_recv_size = 0
            self.init_connect_time = start_time
            self.previous_time = start_time
        if gabriel.Debug.SAVE_IMAGES:
            if not os.path.exists(gabriel.Const.LOG_IMAGES_PATH):
                os.makedirs(gabriel.Const.LOG_IMAGES_PATH)
            self.log_images_counter = 0
            self.log_images_timing = open(os.path.join(
                gabriel.Const.LOG_IMAGES_PATH, "timing.txt"), "w")
        if gabriel.Debug.SAVE_VIDEO:
            self.log_video_writer_created = False        


async def consumer_handler(websocket, path):
    start_time = time.time()
    state = HandlerState(start_time)
    while True:
        raw_input = await websocket.recv()
        input = gabriel_pb2.Input()
        input.ParseFromString(raw_input)

        # TODO Add timing information when
        # gabriel.Debug.TIME_MEASUREMENT is True

        # stats
        if gabriel.Debug.LOG_STAT:
            state.frame_count += 1
            current_time = time.time()
            state.total_recv_size += len(message)
            current_FPS = 1 / (current_time - state.previous_time)
            state.previous_time = current_time
            average_FPS = state.frame_count / (current_time - state.init_connect_time)

            if (state.frame_count % 100 == 0):
                BW = (8 * self.total_recv_size /
                      (current_time - self.init_connect_time) / 1000 / 1000)
                log_msg = ("Video FPS : current(%f), avg(%f), BW(%f Mbps), offloading engine(%d)" % 
                    (current_FPS, average_FPS, BW, len(image_queue_list)))
                LOG.info(log_msg)

        header_data = json.dumps({'style': input.style})
        image_data = input.data

        # put input data in all registered cognitive engine queue
        if input.type == gabriel_pb2.Input.Type.IMAGE:
            for image_queue in image_queue_list:
                if image_queue.full():
                    try:
                        image_queue.get_nowait()
                    except asyncio.QueueEmpty as e:
                        pass
                try:
                    image_queue.put_nowait((header_data, image_data))
                except asyncio.QueueFull as e:
                    pass

        # TODO display input stream for debug purpose
        # TODO write images into files
        # TODO write images into files
                        


async def producer_handler(websocket, path):
    while True:
        (rtn_header, rtn_data) = await result_queue.get()

        rtn_header_json = json.loads(rtn_header)

        # TODO Log time
        # TODO support debug server

        result = gabriel_pb2.Result()
        result.data = rtn_data

        style = rtn_header_json.get('style')
        if style is not None:
            result.style = style

        await websocket.send(result.SerializeToString())

async def handler(websocket, path):
    print('Connect')
    consumer_task = asyncio.ensure_future(
        consumer_handler(websocket, path))
    producer_task = asyncio.ensure_future(
        producer_handler(websocket, path))
    done, pending = await asyncio.wait(
        [consumer_task, producer_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()


def launch():
    start_server = websockets.serve(handler, port=9098)
    asyncio.get_event_loop().run_until_complete(start_server)
    asyncio.get_event_loop().run_forever()
