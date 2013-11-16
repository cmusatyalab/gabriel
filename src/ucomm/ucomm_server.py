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
import time
import SocketServer
import socket
import select
import traceback
import struct
import threading

import log as logging


LOG = logging.getLogger(__name__)


class UCommConst(object):
    RESULT_RECEIVE_PORT     =   10120


class UCommServerError(Exception):
    pass


class UCommHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(UCommHandler, self).setup()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise UCommServerError("Socket is closed")
            data += tmp_data
        return data

    def handle(self):
        try:
            LOG.info("new Offlaoding Engine is connected")
            socket_fd = self.request.fileno()
            input_list = [socket_fd]
            while True:
                inputready, outputready, exceptready = \
                        select.select(input_list, [], [], 0)
                for insocket in inputready:
                    if insocket == socket_fd:
                        self._handle_input_stream()
                time.sleep(0.001)
        except Exception as e:
            #LOG.debug(traceback.format_exc())
            LOG.debug("%s" % str(e))
            LOG.info("Offloading engine is disconnected")
            self.terminate()

    def _handle_input_stream(self):
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        print "received : %s" % header_data


class UCommServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    stopped = False

    def __init__(self, port, handler):
        server_address = ('0.0.0.0', port)
        self.allow_reuse_address = True
        self.handler = handler
        try:
            SocketServer.TCPServer.__init__(self, server_address, handler)
        except socket.error as e:
            sys.stderr.write(str(e))
            sys.stderr.write("Check IP/Port : %s\n" % (str(server_address)))
            sys.exit(1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        LOG.info("* UCOMM server configuration")
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
        
        if self.socket != -1:
            self.socket.close()
        LOG.info("[TERMINATE] Finish ucomm server")


def main():
    ucomm_server = None
    ucomm_server = UCommServer(UCommConst.RESULT_RECEIVE_PORT, UCommHandler)
    ucomm_thread = threading.Thread(target=ucomm_server.serve_forever)
    ucomm_thread.daemon = True

    exit_status = 1
    try:
        ucomm_thread.start()
        while True:
            time.sleep(100)
    except Exception as e:
        sys.stderr.write(str(e))
        exit_status = 1
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        exit_status = 0
    finally:
        if ucomm_server is not None:
            ucomm_server.terminate()
    return exit_status


if __name__ == '__main__':
    ret = main()
    sys.exit(ret)
