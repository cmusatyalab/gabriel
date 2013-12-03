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

import sys
sys.path.insert(0, "../common")
import time
import Queue
import hashlib

from app_proxy import AppProxyBlockingClient
from app_proxy import ResultpublishClient
from app_proxy import get_service_list
from app_proxy import Protocol_client
from app_proxy import SERVICE_META
import struct


class DummyVideoApp(AppProxyBlockingClient):
    THRESHOLD = 90
    long_computation = 0
    short_computation = 0
    total_count = 0

    def get_compute_time(self, data):
        value = int(hashlib.sha1(data).hexdigest(), 16) % 100
        if value >= self.THRESHOLD:
            computation_time = 0.2
            self.long_computation += 1
        else:
            computation_time = 0.02
            self.short_computation += 1
        self.total_count += 1

        if self.total_count % 100 == 0:
            print "Long comutation: %f, short computation: %f" %\
                    (100.0*self.long_computation/self.total_count, \
                    100.0*self.short_computation/self.total_count)
        return computation_time

    def handle(self, header, data):
        s_time = time.time()
        compute_time = self.get_compute_time(data)
        left_time = compute_time - (time.time() - s_time)
        time.sleep(left_time)
        return "dummy"



if __name__ == "__main__":
    output_queue_list = list()

    sys.stdout.write("Finding control VM\n")
    service_list = get_service_list(sys.argv)
    video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
    video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
    acc_ip = service_list.get(SERVICE_META.ACC_TCP_STREAMING_ADDRESS)
    acc_port = service_list.get(SERVICE_META.ACC_TCP_STREAMING_PORT)
    return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

    # dummy video app
    video_client = DummyVideoApp((video_ip, video_port), output_queue_list)
    video_client.start()
    video_client.isDaemon = True

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
        video_client.terminate()
        result_pub.terminate()

