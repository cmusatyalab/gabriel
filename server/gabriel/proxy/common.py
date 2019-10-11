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
try:
	import Queue
except ImportError:
	import queue as Queue
import select
import socket
import struct
import sys
import threading
import time
import traceback

import gabriel
LOG = gabriel.logging.getLogger(__name__)

try:
    str(b'0x1','ascii')
    def mystr(b):
        return str(b, 'ascii')
    def bts(s):
        return bytes(s, 'ascii')
except:
    def mystr(b):
        return str(b)
    def bts(s):
        return bytes(s)

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
        header_json = json.loads(mystr(header_str))

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
            # header can be changed directly in the proxy (design choice made for backward compatibility)
            result = self.handle(header, data) # header is in JSON format
            if result is None: # A special return that marks the result useless
                continue

            ## put return data into output queue
            rtn_json = header
            rtn_json[gabriel.Protocol_client.JSON_KEY_ENGINE_ID] = self.engine_id
            if gabriel.Debug.TIME_MEASUREMENT:
                rtn_json[gabriel.Protocol_measurement.JSON_KEY_APP_SENT_TIME] = time.time()
            self.output_queue.put( (json.dumps(rtn_json), result) )
        LOG.info("[TERMINATE] Finish %s" % str(self))

    def handle(self, header, data): # header is in JSON format
        return None

    def terminate(self):
        self.stop.set()


class MasterProxyThread(threading.Thread):
    '''
    The thread that distributes data to multiple worker threads.
    Similar to @CognitiveProcessThread, it takes input data from @data_queue.
    However, is should implement its own @handle function to decide where the data goes.
    '''
    def __init__(self, data_queue, engine_id = None):
        self.data_queue = data_queue
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
            self.handle(header, data) # header is in JSON format

        LOG.info("[TERMINATE] Finish %s" % str(self))

    def handle(self, header, data): # header is in JSON format
        pass

    def terminate(self):
        self.stop.set()


class ResultPublishClient(gabriel.network.CommonClient):
    """
    This client will publish processed result from @data_queue to the ucomm server.
    """
    def __init__(self, ucomm_addr, data_queue, log_flag = True):
        gabriel.network.CommonClient.__init__(self, ucomm_addr)
        self.data_queue = data_queue
        if not log_flag:
            import logging
            LOG.setLevel(logging.CRITICAL + 1)

    def __repr__(self):
        return "Result Publish Client"

    def _handle_queue_data(self):
        try:
            rtn_header, rtn_data = self.data_queue.get(timeout = 0.0001)
            total_size = len(rtn_header) + len(rtn_data)
            # packet format: total size, header size, header, data
            packet = struct.pack("!II{}s{}s".format(len(rtn_header), len(rtn_data)), total_size, len(rtn_header), bts(rtn_header), rtn_data)
            self.sock.sendall(packet)
            LOG.info("sending result to ucomm: %s" % gabriel.util.print_rtn(json.loads(rtn_header)))
        except Queue.Empty as e:
            pass


class DataPublishHandler(gabriel.network.CommonHandler):
    def setup(self):
        LOG.info("New receiver connected to data stream")

        super(DataPublishHandler, self).setup()
        self.data_queue = multiprocessing.Queue(gabriel.Const.MAX_FRAME_SIZE)

        # receive engine name
        data_size = struct.unpack("!I", self._recv_all(4))[0]
        self.engine_id = self._recv_all(data_size)
        LOG.info("Got engine name: %s" % self.engine_id)
        self.engine_number = self._register_engine(self.data_queue, self.engine_id)

        # send engine sequence number back
        packet = struct.pack("!I", self.engine_number)
        self.request.send(packet)
        self.wfile.flush()

    def __repr__(self):
        return "Data Publish Server"

    def _handle_queue_data(self):
        try:
            (header, data) = self.data_queue.get(timeout = 0.0001)
            header_str = json.dumps(header)

            # send data
            packet = struct.pack("!II%ds%ds" % (len(header_str), len(data)), len(header_str), len(data), bts(header_str), data)
            self.request.send(packet)
            self.wfile.flush()

            # receive result
            header_size = struct.unpack("!I", self._recv_all(4))[0]
            header_str = self._recv_all(header_size)
            header = json.loads(mystr(header_str))
            state_size = struct.unpack("!I", self._recv_all(4))[0]
            state = self._recv_all(state_size)

            header[gabriel.Protocol_client.JSON_KEY_ENGINE_ID] = self.engine_id
            header[gabriel.Protocol_client.JSON_KEY_ENGINE_NUMBER] = self.engine_number

            try:
                self.server.output_queue.put_nowait( (header, state) )
            except Queue.Full as e:
                LOG.error("%s: output queue shouldn't be full" % self)

        except Queue.Empty as e:
            pass

    def _register_engine(self, queue, engine_id):
        '''
        Registers the new engine.
        The data server will publish data to only one engine with the same @engine_id.
        Returns the seq number of current engine among all engines that share the same @engine_id.
        '''
        engine_info = self.server.queue_dict.get(engine_id, None)
        if engine_info is None:
            self.server.queue_dict[engine_id] = {'queues': [queue], 'tokens': [1]}
            return 0
        else:
            engine_info['queues'].append(queue)
            engine_info['tokens'].append(1)
            return len(engine_info['queues']) - 1

    def _unregister_engine(self, queue, engine_id):
        #TODO
        pass

    def terminate(self):
        LOG.info("Offloading engine disconnected from video stream")
        self._unregister_engine(self.data_queue, engine_id)
        super(DataPublishHandler, self).terminate()


class DataPublishServer(gabriel.network.CommonServer):
    def __init__(self, port, handler, queue_dict, output_queue):
        gabriel.network.CommonServer.__init__(self, port, handler) # cannot use super because it's old style class
        LOG.info("* Data publish server(%s) configuration" % str(self.handler))
        LOG.info(" - Open TCP Server at %s" % (str(self.server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)))
        LOG.info("-" * 50)

        self.queue_dict = queue_dict
        self.output_queue = output_queue

    def terminate(self):
        gabriel.network.CommonServer.terminate(self)
