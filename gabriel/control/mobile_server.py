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
import Queue
import select
import socket
import SocketServer
import struct
import sys
import threading
import time
import traceback

import gabriel
LOG = gabriel.logging.getLogger(__name__)

image_queue_list = list()
acc_queue_list = list()
gps_queue_list = list()
result_queue = multiprocessing.Queue()


class MobileCommError(Exception):
    pass


class MobileSensorHandler(gabriel.network.CommonHandler):
    def setup(self):
        super(MobileSensorHandler, self).setup()
        self.stop_queue = multiprocessing.Queue()
        if gabriel.Debug.LOG_STAT:
            self.init_connect_time = None
            self.previous_time = None

    def handle(self):
        LOG.info("Google Glass is connected for (%s)" % str(self))
        if gabriel.Debug.LOG_STAT:
            self.init_connect_time = time.time()
            self.previous_time = time.time()
        super(MobileSensorHandler, self).handle()


class MobileVideoHandler(MobileSensorHandler):
    '''
    The video stream server that
    1) takes MJPEG streams as input, and
    2) put the image data into queues that are then sent to different cognitive engines
    Optionally, it can also syncronize time with client
    '''
    def setup(self):
        super(MobileVideoHandler, self).setup()
        if gabriel.Debug.LOG_STAT:
            self.frame_count = 0
            self.total_recv_size = 0

    def __repr__(self):
        return "Mobile Video Server"

    def _handle_input_data(self):
        ## receive data
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        header_json = json.loads(header_data)

        # if "sync_time" field exists in the header, then this is a time sync request
        # and a local time is returned immediately
        if header_json.get("sync_time") is not None:
            header_json["sync_time"] = int(time.time() * 1000) # in millisecond
            header_data = json.dumps(header_json)
            packet = struct.pack("!I%ds" % len(header_data),
                    len(header_data), header_data)
            self.request.send(packet)
            self.wfile.flush()
            return

        # normal packat with MJPEG image data
        image_size = struct.unpack("!I", self._recv_all(4))[0]
        image_data = self._recv_all(image_size)

        ## the gabriel test app that does nothing...
        if gabriel.Debug.DIRECT_RETURN:
            packet = struct.pack("!I%ds" % len(header_data),
                    len(header_data), header_data)
            self.request.send(packet)
            self.wfile.flush()

        ## add header data for measurement
        if gabriel.Debug.TIME_MEASUREMENT:
            header_json[gabriel.Protocol_measurement.JSON_KEY_CONTROL_RECV_FROM_MOBILE_TIME] = time.time()
            header_data = json.dumps(header_json)

        ## stats
        if gabriel.Debug.LOG_STAT:
            self.frame_count += 1
            current_time = time.time()
            self.total_recv_size += (header_size + img_size + 8)
            current_FPS = 1 / (current_time - self.previous_time)
            self.previous_time = current_time
            average_FPS = self.frame_count / (self.current_time - self.init_connect_time)

            if (self.frame_count % 100 == 0):
                log_msg = "Video FPS : current(%f), avg(%f), BW(%f Mbps), offloading engine(%d)" % \
                        (current_FPS, average_FPS, 8 * self.totoal_recv_size / (current_time - self.init_connect_time) / 1000 / 1000,
                        len(image_queue_list))
                LOG.info(log_msg)

        ## put current image data in all registered cognitive engine queue
        for image_queue in image_queue_list:
            if image_queue.full():
                try:
                    image_queue.get_nowait()
                except Queue.Empty as e:
                    pass
            image_queue.put((header_data, image_data))


## TODO
class MobileAccHandler(MobileSensorHandler):
    def setup(self):
        super(MobileAccHandler, self).setup()

    def __repr__(self):
        return "Mobile Acc Server"

    def _handle_input_data(self):
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        acc_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        acc_data = self._recv_all(acc_size)
        self.frame_count += 1

        # measurement
        self.current_time = time.time()
        self.current_FPS = 1 / (self.current_time - self.previous_time)
        self.average_FPS = self.frame_count / (self.current_time -
                self.init_connect_time)
        self.previous_time = self.current_time

        if (self.frame_count % 100 == 0):
            msg = "ACC FPS : current(%f), average(%f), offloading Engine(%d)" % \
                    (self.current_FPS, self.average_FPS, len(acc_queue_list))
            LOG.info(msg)

        try:
            for acc_queue in acc_queue_list:
                if acc_queue.full() is True:
                    acc_queue.get_nowait()
                acc_queue.put_nowait((header_data, acc_data))
        except Queue.Empty as e:
            pass
        except Queue.Full as e:
            pass

    def _handle_output_result(self):
        """ control message
        """
        pass


class MobileResultHandler(MobileSensorHandler):
    def setup(self):
        super(MobileResultHandler, self).setup()

        # a global queue that contains final messages sent back to the client
        global result_queue
        # flush out old result at Queue
        while not result_queue.empty():
            result_queue.get()
        self.data_queue = result_queue

        if gabriel.Debug.TIME_MEASUREMENT:
            self.time_breakdown_log = open("log-time-breakdown.txt", "w")

    def __repr__(self):
        return "Mobile Result Handler"

    def _handle_queue_data(self):
        try:
            rtn_data = self.data_queue.get(timeout = 0.0001)

            # log measured time
            if gabriel.Debug.TIME_MEASUREMENT:
                rtn_json = json.loads(rtn_data)
                frame_id = rtn_json[gabriel.Protocol_client.JSON_KEY_FRAME_ID]
                result_str = rtn_json[gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE]
                result_json = json.loads(result_str)
                now = time.time()

                control_recv_from_mobile_time = rtn_json.get(gabriel.Protocol_measurement.JSON_KEY_CONTROL_RECV_FROM_MOBILE_TIME)
                app_recv_time = rtn_json.get(gabriel.Protocol_measurement.JSON_KEY_APP_RECV_TIME, -1)
                app_sent_time = rtn_json.get(gabriel.Protocol_measurement.JSON_KEY_APP_SENT_TIME, -1)
                ucomm_recv_time = rtn_json.get(gabriel.Protocol_measurement.JSON_KEY_UCOMM_RECV_TIME, -1)
                ucomm_sent_time = rtn_json.get(gabriel.Protocol_measurement.JSON_KEY_UCOMM_SENT_TIME, -1)

                symbolic_done_time = result_json.get(gabriel.Protocol_measurement.JSON_KEY_APP_SYMBOLIC_TIME, -1)

                # no need to send the time info back to the client
                del rtn_json[gabriel.Protocol_measurement.JSON_KEY_CONTROL_RECV_FROM_MOBILE_TIME]
                del rtn_json[gabriel.Protocol_measurement.JSON_KEY_APP_SENT_TIME]
                del rtn_json[gabriel.Protocol_measurement.JSON_KEY_APP_RECV_TIME]
                del rtn_json[gabriel.Protocol_measurement.JSON_KEY_UCOMM_RECV_TIME]
                del rtn_json[gabriel.Protocol_measurement.JSON_KEY_UCOMM_SENT_TIME]
                try:
                    del header[Protocol_measurement.JSON_KEY_APP_SYMBOLIC_TIME]
                except KeyError:
                    pass

                if self.time_breakdown_log is not None:
                    self.time_breakdown_log.write("%d\t%f\t%f\t%f\t%f\t%f\t%f\t%f\n" %
                            (frame_id, control_recv_from_mobile_time, app_recv_time, symbolic_time, app_sent_time, ucomm_recv_time, ucomm_sent_time, now))

                rtn_data = json.dumps(rtn_json)

            packet = struct.pack("!I%ds" % len(rtn_data),
                    len(rtn_data), rtn_data)
            self.request.send(packet)
            self.wfile.flush()
            LOG.info("message sent to the Glass: %s", gabriel.util.print_rtn(rtn_json))

        except Queue.Empty:
            LOG.warning("data queue shouldn't be empty! - %s" % str(self))


class MobileCommServer(gabriel.network.CommonServer):
    def __init__(self, port, handler):
        gabriel.network.CommonServer.__init__(self, port, handler) # cannot use super because it's old style class
        LOG.info("* Mobile server(%s) configuration" % str(self.handler))
        LOG.info(" - Open TCP Server at %s" % (str(self.server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def terminate(self):
        gabriel.network.CommonServer.terminate(self)


def main():
    video_server = MobileCommServer(gabriel.Const.MOBILE_SERVER_VIDEO_PORT, MobileVideoHandler)
    video_thread = threading.Thread(target=video_server.serve_forever)
    video_thread.daemon = True

    #acc_server = MobileCommServer(gabriel.Const.MOBILE_SERVER_ACC_PORT, MobileVideoHandler)
    #acc_thread = threading.Thread(target=acc_server.serve_forever)
    #acc_thread.daemon = True

    try:
        video_thread.start()
        #acc_thread.start()
        while True:
            time.sleep(100)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        video_server.terminate()
        #acc_server.terminate()
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(str(e))
        video_server.terminate()
        #acc_server.terminate()
        sys.exit(1)
    else:
        video_server.terminate()
        #acc_server.terminate()
        sys.exit(0)


if __name__ == '__main__':
    main()
