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
import sys
import json

import SocketServer
import threading
import select
import traceback
import Queue
import struct
import socket

import log as logging
from config import Const as Const
from protocol import Protocol_client
from mobile_server import result_queue


LOG = logging.getLogger(__name__)


class UCommError(Exception):
    pass

class UCommHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(UCommHandler, self).setup()
        self.stop = threading.Event()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise UCommError("Socket is closed at %s" % str(self))
            data += tmp_data
        return data

    def _handle_input_data(self):
        pass

    def _handle_output_result(self):
        ret_size = self._recv_all(4)
        ret_size = struct.unpack("!I", ret_size)[0]
        result_data = self._recv_all(ret_size)
        self.result_queue.put(str(result_data))

    def handle(self):
        global image_queue_list
        try:
            LOG.info("User communication module is connected")
            self.init_connect_time = time.time()
            self.previous_time = time.time()

            socket_fd = self.request.fileno()
            input_list = [socket_fd]
            output_list = [socket_fd]
            except_list = [socket_fd]

            while(not self.stop.wait(0.0001)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, except_list, 0)
                for s in inputready:
                    if s == socket_fd:
                        self._handle_input_data()
                for output in outputready:
                    if output == socket_fd:
                        self._handle_output_result()
                for e in exceptready:
                    if e == socket_fd:
                        break

                if not (inputready or outputready or exceptready):
                    continue
                time.sleep(0.0001)
        except Exception as e:
            #LOG.info(traceback.format_exc())
            LOG.info("%s\n" % str(e))
            LOG.info("UComm module is disconnected")
        if self.socket != -1:
            self.socket.close()

    def terminate(self):
        self.stop.set()


class UCommServer(SocketServer.TCPServer):
    stopped = False

    def __init__(self, port, handler):
        server_address = ('0.0.0.0', port)
        self.allow_reuse_address = True
        self.handler = handler
        try:
            SocketServer.TCPServer.__init__(self, server_address,
                    handler)
        except socket.error as e:
            sys.stderr.write(str(e))
            sys.stderr.write("Check IP/Port : %s\n" % (str(server_address)))
            sys.exit(1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        LOG.info("* UComm server configuration")
        LOG.info(" - Open TCP Server at %s" % (str(server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(
                    socket.IPPROTO_TCP,
                    socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def serve_forever(self):
        while not self.stopped:
            self.handle_request()

    def handle_error(self, request, client_address):
        pass

    def terminate(self):
        self.server_close()
        self.stopped = True

        # close all thread
        if self.socket != -1:
            self.socket.close()
        LOG.info("[TERMINATE] Finish UComm server connection")


def main():
    ucomm_server = UCommServer(Const.UCOMM_COMMUNICATE_PORT, UCommHandler)
    ucomm_thread = threading.Thread(target=ucomm_server.serve_forever)
    ucomm_thread.daemon = True

    try:
        ucomm_thread.start()
    except Exception as e:
        sys.stderr.write(str(e))
        ucomm_server.terminate()
        sys.exit(1)
    except KeyboardInterrupt as e:
        sys.stderr.write(str(e))
        ucomm_server.terminate()
        sys.exit(1)
    else:
        ucomm_server.terminate()
        sys.exit(0)


if __name__ == '__main__':
    main()
