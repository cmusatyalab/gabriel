#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Kiryong Ha <krha@cmu.edu>
#           Zhuo Chen <zhuoc@cs.cmu.edu>
#
#   Copyright (C) 2011-2013 Carnegie Mellon University
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#

import json
import multiprocessing
import select
import socket
import struct
import sys
import threading
import time
import traceback
import asyncio

import sys

if (sys.version_info > (3, 0)):
    import queue as Queue
else:
    import Queue

import gabriel
import gabriel.control
LOG = gabriel.logging.getLogger(__name__)


class EngineServerError(Exception):
    pass


class SensorPublishHandler(gabriel.network.CommonHandler):
    def setup(self):
        super(SensorPublishHandler, self).setup()


class VideoPublishHandler(SensorPublishHandler):
    def setup(self):
        print('setup start')
        super(VideoPublishHandler, self).setup()
        self.data_queue = gabriel.control.image_queue_list[0]
        print('setup end')

    def __repr__(self):
        return "Video Publish Server"

    def handle(self):
        LOG.info("New offloading engine connected to video stream")
        super(VideoPublishHandler, self).handle()

    def _handle_queue_data(self):
        try:
            print('data queue size', self.data_queue.qsize())
            (header_data, image_data) = self.data_queue.get_nowait()
            print('Start publish')

            header_json = json.loads(header_data.decode('utf-8'))
            header_json.update({gabriel.Protocol_sensor.JSON_KEY_SENSOR_TYPE : gabriel.Protocol_sensor.JSON_VALUE_SENSOR_TYPE_JPEG})
            header_data = json.dumps(header_json)

            packet = struct.pack("!II%ds%ds" % (len(header_data), len(image_data)),
                                 len(header_data), len(image_data), bytes(header_data, 'utf-8'), image_data)
            self.request.send(packet)
            self.wfile.flush()
        except asyncio.QueueEmpty as e:
            time.sleep(0.1)
        except Exception as e:
            traceback.print_stack()

    def terminate(self):
        LOG.info("Offloading engine disconnected from video stream")
        gabriel.control.image_queue_list.remove(self.data_queue)
        super(VideoPublishHandler, self).terminate()


class AccPublishHandler(SensorPublishHandler):
    def setup(self):
        super(AccPublishHandler, self).setup()
        self.data_queue = multiprocessing.Queue(gabriel.Const.MAX_FRAME_SIZE)
        gabriel.control.acc_queue_list.append(self.data_queue)

    def __repr__(self):
        return "ACC Publish Server"

    def handle(self):
        LOG.info("New offloading engine connected to ACC stream")
        super(AccPublishHandler, self).handle()

    def _handle_queue_data(self):
        try:
            (header_data, acc_data) = self.data_queue.get(timeout = 0.0001)

            header_json = json.loads(header_data.decode('utf-8'))
            header_json.update({gabriel.Protocol_sensor.JSON_KEY_SENSOR_TYPE : gabriel.Protocol_sensor.JSON_VALUE_SENSOR_TYPE_ACC})
            header_data = json.dumps(header_json)

            packet = struct.pack("!II%ds%ds" % (len(header_data), len(acc_data)),
                    len(header_data), len(acc_data), header_data, acc_data)
            self.request.send(packet)
            self.wfile.flush()
        except Queue.Empty as e:
            pass

    def terminate(self):
        LOG.info("Offloading engine disconnected from ACC stream")
        gabriel.control.acc_queue_list.remove(self.data_queue)
        super(AccPublishHandler, self).setup()


class AudioPublishHandler(SensorPublishHandler):
    def setup(self):
        super(AudioPublishHandler, self).setup()
        self.data_queue = multiprocessing.Queue(gabriel.Const.MAX_FRAME_SIZE)
        gabriel.control.audio_queue_list.append(self.data_queue)

    def __repr__(self):
        return "Audio Publish Server"

    def handle(self):
        LOG.info("New offloading engine connected to audio stream")
        super(AudioPublishHandler, self).handle()

    def _handle_queue_data(self):
        try:
            (header_data, audio_data) = self.data_queue.get(timeout = 0.0001)

            header_json = json.loads(header_data.decode('utf-8'))
            header_json.update({gabriel.Protocol_sensor.JSON_KEY_SENSOR_TYPE : gabriel.Protocol_sensor.JSON_VALUE_SENSOR_TYPE_AUDIO})
            header_data = json.dumps(header_json)

            packet = struct.pack("!II%ds%ds" %(len(header_data), len(audio_data)),
                    len(header_data), len(audio_data), header_data, audio_data)
            self.request.send(packet)
            self.wfile.flush()
        except Queue.Empty as e:
            pass

    def terminate(self):
        LOG.info("Offloading engine disconnected from audio stream")
        gabriel.control.audio_queue_list.remove(self.data_queue)
        super(AudioPublishHandler, self).terminate()


class SensorPublishServer(gabriel.network.CommonServer):
    def __init__(self, port, handler):
        gabriel.network.CommonServer.__init__(self, port, handler) # cannot use super because it's old style class
        LOG.info("* Application server(%s) configuration" % str(self.handler))
        LOG.info(" - Open TCP Server at %s" % (str(self.server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def terminate(self):
        gabriel.network.CommonServer.terminate(self)


## TODO
class OffloadingEngineMonitor(threading.Thread):
    def __init__(self, v_queuelist, a_queuelist, g_queuelist, result_queue):
        self.stop = threading.Event()
        threading.Thread.__init__(self, target=self.monitor)
        self.v_queuelist = v_queuelist
        self.a_queuelist = a_queuelist
        self.g_queuelist = g_queuelist
        self.result_queue = result_queue

        self.count_prev_video_app = 0
        self.count_prev_acc_app = 0
        self.count_prev_gps_app = 0
        self.count_cur_video_app = 0
        self.count_cur_acc_app = 0
        self.count_cur_gps_app = 0

    def _inject_token(self):
        '''
        if self.result_queue.empty() == True:
            LOG.info("Inject token to start receiving data from the Glass")
            header = json.dumps({
                Protocol_client.TOKEN_INJECT_KEY: int(Const.TOKEN_INJECTION_SIZE),
                })
            self.result_queue.put(header)
        '''
        pass

    def monitor(self):
        while(not self.stop.wait(0.01)):
            self.count_cur_video_app = len(self.v_queuelist)
            self.count_cur_acc_app = len(self.a_queuelist)
            self.count_cur_gps_app = len(self.g_queuelist)

            if (self.count_prev_video_app == 0 and self.count_cur_video_app > 0) or \
                    (self.count_prev_acc_app == 0 and self.count_cur_acc_app > 0) or \
                    (self.count_prev_gps_app == 0 and self.count_cur_gps_app > 0):
                self._inject_token()
            self.count_prev_video_app = self.count_cur_video_app
            self.count_prev_acc_app = self.count_cur_acc_app
            self.count_prev_gps_app = self.count_cur_gps_app

    def terminate(self):
        self.stop.set()


def main():
    video_server = SensorPublishServer(gabriel.Const.PUBLISH_SERVER_VIDEO_PORT, VideoPublishHandler)
    video_thread = threading.Thread(target = video_server.serve_forever)
    video_thread.daemon = True

    acc_server = SensorPublishServer(gabriel.Const.PUBLISH_SERVER_ACC_PORT, AccPublishHandler)
    acc_thread = threading.Thread(target = acc_server.serve_forever)
    acc_thread.daemon = True

    audio_server = SensorPublishServer(gabriel.Const.PUBLISH_SERVER_AUDIO_PORT, AudioPublishHandler)
    audio_thread = threading.Thread(target = audio_server.serve_forever)
    audio_thread.daemon = True

    try:
        video_thread.start()
        acc_thread.start()
        audio_thread.start()
        while True:
            time.sleep(100)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        video_server.terminate()
        acc_server.terminate()
        audio_server.terminate()
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(str(e))
        video_server.terminate()
        acc_server.terminate()
        audio_server.terminate()
        sys.exit(1)
    else:
        video_server.terminate()
        acc_server.terminate()
        audio_server.terminate()
        sys.exit(0)


if __name__ == '__main__':
    main()
