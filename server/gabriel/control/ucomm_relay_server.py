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

import gabriel
LOG = gabriel.logging.getLogger(__name__)


class UCommError(Exception):
    pass


class UCommRelayHandler(gabriel.network.CommonHandler):
    '''
    The server that is connected with ucomm module.
    It receives return messages and put them into @result_queue,
    which are then picked up by mobile result handler (in mobile_server.py) to be sent to the mobile device
    '''
    def setup(self):
        super(UCommRelayHandler, self).setup()

    def __repr__(self):
        return "UCOMM Relay Handler"

    def handle(self):
        LOG.info("User communication module is connected")
        super(UCommRelayHandler, self).handle()

    def _handle_input_data(self):
        rtn_size = struct.unpack("!I", self._recv_all(4))[0]
        rtn_header_size = struct.unpack("!I", self._recv_all(4))[0]
        rtn_header = self._recv_all(rtn_header_size)
        rtn_data = self._recv_all(rtn_size-rtn_header_size)
        asyncio.run_coroutine_threadsafe(
            self._handle_input_data_helper(rtn_header, rtn_data),
            gabriel.control.websocket_server.event_loop)
        
    async def _handle_input_data_helper(self, rtn_header, rtn_data):
        await gabriel.control.result_queue.put((rtn_header, rtn_data))

        # control messages
        rtn_header_json = json.loads(rtn_header.decode('utf-8'))
        message_control = rtn_header_json.get('control', None)
        if message_control is not None:
            message_control = str(message_control) # this will be unicode otherwise
            gabriel.control.command_queue.put(message_control)


class UCommRelayServer(gabriel.network.CommonServer):
    def __init__(self, port, handler):
        gabriel.network.CommonServer.__init__(self, port, handler) # cannot use super because it's old style class
        LOG.info("* UComm relay server(%s) configuration" % str(self.handler))
        LOG.info(" - Open TCP Server at %s" % (str(self.server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def terminate(self):
        gabriel.network.CommonServer.terminate(self)


def main():
    ucomm_relay_server = UCommRelayServer(gabriel.Const.UCOMM_COMMUNICATE_PORT, UCommRelayHandler)
    ucomm_relay_thread = threading.Thread(target = ucomm_relay_server.serve_forever)
    ucomm_relay_thread.daemon = True

    try:
        ucomm_relay_thread.start()
        while True:
            time.sleep(100)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        ucomm_relay_server.terminate()
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(str(e))
        ucomm_relay_server.terminate()
        sys.exit(1)
    else:
        ucomm_relay_server.terminate()
        sys.exit(0)


if __name__ == '__main__':
    main()
