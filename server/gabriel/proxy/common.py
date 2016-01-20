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
import struct
import sys
import threading
import time
import traceback

import gabriel
LOG = gabriel.logging.getLogger(__name__)


class ProxyError(Exception):
    pass


class SensorReceiveClient(gabriel.network.CommonClient):
    """
    This client will receive data from the control server as much as possible.
    And put the data into the @output_queue, so that the other thread (@CognitiveProcessThread) can use the data
    """
    def __init__(self, control_addr, output_queue):
        gabriel.network.CommonClient.__init__(self, control_addr)
        self.output_queue = output_queue

    def __repr__(self):
        return "Sensor Receive Client"

    def _handle_input_data(self):
        # receive data from control VM
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        data_size = struct.unpack("!I", self._recv_all(4))[0]
        header_str = self._recv_all(header_size)
        data = self._recv_all(data_size)
        header_json = json.loads(header_str)

        # add header data for measurement
        if gabriel.Debug.TIME_MEASUREMENT:
            header_json[gabriel.Protocol_measurement.JSON_KEY_APP_RECV_TIME] = time.time()

        # token buffer - discard if the token(queue) is not available
        if self.output_queue.full():
            try:
                self.output_queue.get_nowait()
            except Queue.Empty as e:
                pass
        self.output_queue.put((header_json, data))


class CognitiveProcessThread(threading.Thread):
    '''
    The thread that does real processing.
    It takes input data from @data_queue and puts output data into @output_queue.
    An interesting cognitive engine should implement its own @handle function.
    '''
    def __init__(self, data_queue, output_queue, engine_id = None):
        self.data_queue = data_queue
        self.output_queue = output_queue
        self.engine_id = engine_id

        self.stop = threading.Event()

        threading.Thread.__init__(self, target = self.run)

    def __repr__(self):
        return "Cognitive Processing Thread"

    def run(self):
        while(not self.stop.wait(0.0001)):
            try:
                (header, data) = self.data_queue.get(timeout = 0.0001)
                if header is None or data is None:
                    LOG.warning("header or data in data_queue is not valid!")
                    continue
            except Queue.Empty as e:
                continue

            ## the real processing
            result = self.handle(header, data) # header is in JSON format

            ## put return data into output queue
            rtn_json = header
            rtn_json[gabriel.Protocol_client.JSON_KEY_ENGINE_ID] = self.engine_id
            rtn_json[gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE] = result
            if gabriel.Debug.TIME_MEASUREMENT:
                rtn_json[gabriel.Protocol_measurement.JSON_KEY_APP_SENT_TIME] = time.time()
            self.output_queue.put(json.dumps(rtn_json))
        LOG.info("[TERMINATE] Finish %s" % str(self))

    def handle(self, header, data): # header is in JSON format
        return None

    def terminate(self):
        self.stop.set()


class ResultPublishClient(gabriel.network.CommonClient):
    """
    This client will publish processed result from @data_queue to the ucomm server.
    """
    def __init__(self, ucomm_addr, data_queue):
        gabriel.network.CommonClient.__init__(self, ucomm_addr)
        self.data_queue = data_queue

    def __repr__(self):
        return "Result Publish Client"

    def _handle_queue_data(self):
        try:
            rtn_data = self.data_queue.get(timeout = 0.0001)
            packet = struct.pack("!I%ds" % len(rtn_data), len(rtn_data), rtn_data)
            self.sock.sendall(packet)
            LOG.info("sending result to ucomm: %s" % gabriel.util.print_rtn(json.loads(rtn_data)))
        except Queue.Empty as e:
            pass
