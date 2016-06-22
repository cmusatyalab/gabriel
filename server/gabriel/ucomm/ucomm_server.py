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

result_queue = multiprocessing.Queue()


class UcommCommError(Exception):
    pass


class UCommServerHandler(gabriel.network.CommonHandler):
    '''
    The ucomm server receives connection from all offloading engines.
    It receives return messages and put them into @result_queue,
    which are then picked up by lt handler (in mobile_server.py) to be sent to the mobile device
    '''
    def __repr__(self):
        return "UCOMM Server"

    def handle(self):
        LOG.info("new Offlaoding Engine is connected")
        super(UCommServerHandler, self).handle()

    def _handle_input_data(self):
        rtn_size = struct.unpack("!I", self._recv_all(4))[0]
        rtn_header_size = struct.unpack("!I", self._recv_all(4))[0]
        rtn_header = self._recv_all(rtn_header_size)
        rtn_data = self._recv_all(rtn_size-rtn_header_size)
        rtn_header_json = json.loads(rtn_header)

        # check if engine id is provided
        engine_id = rtn_header_json.get(gabriel.Protocol_client.JSON_KEY_ENGINE_ID, None)
        if engine_id is None:
            rtn_header_json[gabriel.Protocol_client.JSON_KEY_ENGINE_ID] = str(self.request.fileno())

        if gabriel.Debug.TIME_MEASUREMENT:
            rtn_header_json[gabriel.Protocol_measurement.JSON_KEY_UCOMM_RECV_TIME] = time.time()

        # the real work, put the return message into the queue
        result_queue.put( (json.dumps(rtn_header_json), rtn_data) )


class UCommServer(gabriel.network.CommonServer):
    def __init__(self, port, handler):
        gabriel.network.CommonServer.__init__(self, port, handler) # cannot use super because it's old style class
        LOG.info("* UCOMM server configuration")
        LOG.info(" - Open TCP Server at %s" % (str(self.server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def terminate(self):
        gabriel.network.CommonServer.terminate(self)


class ResultForwardingClient(gabriel.network.CommonClient):
    """
    This client will forward offloading engine's processed
    result (from @result_queue) to the control VM.
    Also, it marks any duplicated results.
    """
    def __init__(self, control_address):
        gabriel.network.CommonClient.__init__(self, control_address)

        # info about most recently forwarded message
        self.previous_sent_time_dict = dict()
        self.previous_sent_dict = dict()

        self.data_queue = result_queue

        LOG.info("Result forwarding thread created")

    def __repr__(self):
        return "Result Forwarding Client"

    def terminate(self):
        gabriel.network.CommonClient.terminate(self)

    def _handle_queue_data(self):
        try:
            ## get data and result
            forward_header, forward_data = self.data_queue.get(timeout = 0.0001)
            forward_header_json = json.loads(forward_header)
            engine_id = forward_header_json.get(gabriel.Protocol_client.JSON_KEY_ENGINE_ID, None)

            ## mark duplicate message
            prev_sent_data = self.previous_sent_dict.get(engine_id, None)
            if prev_sent_data is not None and prev_sent_data.lower() == forward_data.lower():
                prev_sent_time = self.previous_sent_time_dict.get(engine_id, 0)
                time_diff = time.time() - prev_sent_time
                if time_diff < gabriel.Const.DUPLICATE_MIN_INTERVAL:
                    forward_header_json[gabriel.Protocol_result.JSON_KEY_STATUS] = "duplicate"
            self.previous_sent_time_dict[engine_id] = time.time()
            self.previous_sent_dict[engine_id] = forward_data

            ## time measurement
            if gabriel.Debug.TIME_MEASUREMENT:
                forward_header_json[gabriel.Protocol_measurement.JSON_KEY_UCOMM_SENT_TIME] = time.time()

            ## send packet to control VM
            forward_header = json.dumps(forward_header_json)
            total_size = len(forward_header) + len(forward_data)            
            packet = struct.pack("!II{}s{}s".format(len(forward_header),len(forward_data)), total_size, len(forward_header), forward_header, forward_data)
            self.sock.sendall(packet)
            LOG.info("forward the result: %s" % gabriel.util.print_rtn(forward_header))

        except Queue.Empty as e:
            pass


def main():
    ucomm_server = UCommServer(gabriel.Const.UCOMM_SERVER_PORT, UCommServerHandler)
    ucomm_server_thread = threading.Thread(target = ucomm_server.serve_forever)
    ucomm_server_thread.daemon = True

    try:
        ucomm_server_thread.start()
        while True:
            time.sleep(100)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        ucomm_server.terminate()
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(str(e))
        ucomm_server.terminate()
        sys.exit(1)
    else:
        ucomm_server.terminate()
        sys.exit(0)


if __name__ == '__main__':
    main()
