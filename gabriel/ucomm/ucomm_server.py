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
        super(UcommServerHandler, self).handle()

    def _handle_input_stream(self):
        rtn_size = struct.unpack("!I", self._recv_all(4))[0]
        rtn_data = self._recv_all(rtn_size)
        rtn_json = json.loads(rtn_data)

        # check if engine id is provided
        engine_id = header_json.get(gabriel.Protocol_client.JSON_KEY_ENGINE_ID, None)
        if engine_id is None:
            rtn_json[gabriel.Protocol_client.JSON_KEY_ENGINE_ID] = str(self.request.fileno())

        if gabriel.Debug.TIME_MEASUREMENT:
            rtn_json[gabriel.Protocol_measurement.JSON_KEY_UCOMM_RECV_TIME] = time.time()

        # the real work, put the return message into the queue
        result_queue.put(json.dumps(header_json))


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

    def terminate(self):
        gabriel.network.CommonClient.terminate(self)

    def _handle_queue_data(self):
        try:
            ## get data and result
            forward_data = self.data_queue.get(timeout = 0.0001)
            forward_json = json.loads(forward_data)
            result_str = forward_json.get(gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE, None)
            engine_id = forward_json.get(gabriel.Protocol_client.JSON_KEY_ENGINE_ID, None)

            ## get result status
            # convert simple "nothing" into the right json format for backward compatibility
            if result_str is None or len(result_str.strip()) == 0 or result_str == 'nothing':
                result_json = {'status': 'nothing'}
            else:
                result_json = json.loads(result_str)
            status = result_json.get('status')

            ## move symbolic time from result json into rtn_json (forward_json)
            if gabriel.Debug.TIME_MEASUREMENT:
                symbolic_time = result_json.get(gabriel.Protocol_measurement.JSON_KEY_APP_SYMBOLIC_TIME, -1)
                try:
                    del result_json[gabriel.Protocol_measurement.JSON_KEY_APP_SYMBOLIC_TIME]
                    result_str = json.dumps(result_json)
                except KeyError:
                    pass
                forward_json[gabriel.Protocol_measurement.JSON_KEY_APP_SYMBOLIC_TIME] = symbolic_time

            ## mark duplicate message
            if status == "success":
                prev_sent_data = self.previous_sent_dict.get(engine_id, None)
                if prev_sent_data is not None and prev_sent_data.lower() == result_str.lower():
                    prev_sent_time = self.previous_sent_time_dict.get(engine_id, 0)
                    time_diff = time.time() - prev_sent_time
                    if time_diff < gabriel.Const.DUPLICATE_MIN_INTERVAL:
                        result_json['status'] = "duplicate"
                self.previous_sent_time_dict[engine_name] = time.time()
                self.previous_sent_dict[engine_name] = result_str

            result_str = json.dumps(result_json)
            forward_json[gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE] = result_str

            ## time measurement
            if gabriel.Debug.TIME_MEASUREMENT:
                forward_json[gabriel.Protocol_measurement.JSON_KEY_UCOMM_SENT_TIME] = time.time()

            ## send packet to control VM
            forward_data = json.dumps(forward_json)
            packet = struct.pack("!I%ds" % len(forward_data), len(forward_data), forward_data)
            self.sock.sendall(packet)
            LOG.info("forward the result: %s" % output)

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
