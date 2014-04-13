#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Kiryong Ha <krha@cmu.edu>
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

import time
import Queue
import struct
import os
import sys
if os.path.isdir("../gabriel") is True:
    sys.path.insert(0, "..")

from gabriel.proxy.common import AppProxyStreamingClient
from gabriel.proxy.common import AppProxyThread
from gabriel.proxy.common import ResultpublishClient
from gabriel.proxy.common import Protocol_measurement
from gabriel.proxy.common import get_service_list
from gabriel.common.config import ServiceMeta as SERVICE_META
from gabriel.common.config import Const


class DummyVideoApp(AppProxyThread):

    def handle(self, header, data):
        # PERFORM Cognitive Assistant Processing
        sys.stdout.write("processing: ")
        sys.stdout.write("%s\n" % header)
        result = "dummy"
        return result


class DummyAccApp(AppProxyThread):
    def chunks(self, l, n):
        for i in xrange(0, len(l), n):
            yield l[i:i + n]

    def handle(self, header, acc_data):
        ACC_SEGMENT_SIZE = 16# (int, float, float, float)
        for chunk in self.chunks(acc_data, ACC_SEGMENT_SIZE):
            (acc_time, acc_x, acc_y, acc_z) = struct.unpack("!ifff", chunk)
            print "time: %d, acc_x: %f, acc_y: %f, acc_x: %f" % \
                    (acc_time, acc_x, acc_y, acc_z)
        return None


if __name__ == "__main__":
    result_queue = list()

    sys.stdout.write("Discovery Control VM\n")
    service_list = get_service_list(sys.argv)
    video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
    video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
    acc_ip = service_list.get(SERVICE_META.ACC_TCP_STREAMING_ADDRESS)
    acc_port = service_list.get(SERVICE_META.ACC_TCP_STREAMING_PORT)
    return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

    # image receiving thread
    video_frame_queue = Queue.Queue(Const.APP_LEVEL_TOKEN_SIZE)
    print "TOKEN SIZE OF OFFLOADING ENGINE: %d" % Const.APP_LEVEL_TOKEN_SIZE
    video_client = AppProxyStreamingClient((video_ip, video_port), video_frame_queue)
    video_client.start()
    video_client.isDaemon = True
    dummy_video_app = DummyVideoApp(video_frame_queue, result_queue, \
            app_id=Protocol_measurement.APP_DUMMY) # dummy app for image processing
    dummy_video_app.start()
    dummy_video_app.isDaemon = True

    # dummy acc app
    acc_client = None
    acc_app = None
    acc_queue = Queue.Queue(1)
    #acc_client = AppProxyStreamingClient((acc_ip, acc_port), acc_queue)
    #acc_client.start()
    #acc_client.isDaemon = True
    #acc_app = DummyAccApp(acc_queue, result_queue)
    #acc_app.start()
    #acc_app.isDaemon = True

    # result pub/sub
    result_pub = ResultpublishClient(return_addresses, result_queue)
    result_pub.start()
    result_pub.isDaemon = True

    try:
        while True:
            time.sleep(1)
    except Exception as e:
        pass
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
    finally:
        if video_client is not None:
            video_client.terminate()
        if dummy_video_app is not None:
            dummy_video_app.terminate()
        if acc_client is not None:
            acc_client.terminate()
        if acc_app is not None:
            acc_app.terminate()
        result_pub.terminate()

