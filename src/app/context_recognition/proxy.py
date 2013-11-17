#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Zhuo Chen <zhuoc@cs.cmu.edu>
#           Kiryong Ha <krha@cmu.edu>
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

import sys
sys.path.insert(0, "../common")
import time
import Queue

from app_proxy import AppProxyStreamingClient
from app_proxy import AppProxyThread
from app_proxy import ResultpublishClient
from app_proxy import get_service_list
from app_proxy import SERVICE_META
import struct

from analysis import extract_feature
from analysis import classify

WID_SIZE = 50
OVERLAP = 25

class DummyAccApp(AppProxyThread):
    def chunks(self, l, n):
        for i in xrange(0, len(l), n):
            yield l[i:i + n]

    def handle(self, header, acc_data):
        ACC_SEGMENT_SIZE = 16# (int, float, float, float)
        global acc_data_list
        for chunk in self.chunks(acc_data, ACC_SEGMENT_SIZE):
            (acc_time, acc_x, acc_y, acc_z) = struct.unpack("!ifff", chunk)
            #print "time: %d, acc_x: %f, acc_y: %f, acc_x: %f" % \
                    #                (acc_time, acc_x, acc_y, acc_z)
            acc_data_list.append([acc_time, acc_x, acc_y, acc_z])
            if len(acc_data_list) == WID_SIZE:
                feature_levels, feature_level0 = extract_feature(acc_data_list)
                activity = classify(feature_levels, feature_level0)
                
                for i in xrange(WID_SIZE - OVERLAP):
                    del(acc_data_list[0])
    
                print activity 
                return activity

        return None

def init():
    global acc_data_list
    acc_data_list = []

if __name__ == "__main__":
    init()

    output_queue_list = list()

    sys.stdout.write("Finding control VM\n")
    service_list = get_service_list(sys.argv)
    video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
    video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
    acc_ip = service_list.get(SERVICE_META.ACC_TCP_STREAMING_ADDRESS)
    acc_port = service_list.get(SERVICE_META.ACC_TCP_STREAMING_PORT)
    return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

    # dummy acc app
    acc_queue = Queue.Queue(1)
    acc_client = AppProxyStreamingClient((acc_ip, acc_port), acc_queue)
    acc_client.start()
    acc_client.isDaemon = True
    acc_app = DummyAccApp(acc_queue, output_queue_list)
    acc_app.start()
    acc_app.isDaemon = True

    # result pub/sub
    result_pub = ResultpublishClient(return_addresses, output_queue_list)
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
        acc_client.terminate()
        acc_app.terminate()
        result_pub.terminate()

