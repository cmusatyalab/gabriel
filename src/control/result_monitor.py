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

import os
import sys
import tempfile
from config import Const

import time
import SocketServer
import socket
import select
import struct
import threading
import log as logging


LOG = logging.getLogger(__name__)
offload_engine_list = list()


class OffloadingEngineInfo(object):
    def __init__(self, name):
        self.name = name
        self.FPS = 0.0
        self.first_recv_time = time.time()
        self.last_recv_time = time.time()


class ResultMonitorError(Exception):
    pass


class ResultMonitorHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        global offload_engine_list
        super(ResultMonitorHandler, self).setup()
        self.info = OffloadingEngineInfo(self.request.fileno())
        offload_engine_list.append(self.info)
        self.stop_file= tempfile.TemporaryFile(prefix="gabriel-threadfd-")

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise ResultMonitorError("Socket is closed")
            data += tmp_data
        return data

    def handle(self):
        try:
            LOG.info("new Offlaoding Engine is connected")
            stopfd = self.stop_file.fileno()
            socket_fd = self.request.fileno()
            input_list = [socket_fd, stopfd]
            output_list = [socket_fd, stopfd]
            except_list = [socket_fd, stopfd]
            while True:
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, except_list)
                for insocket in inputready:
                    if insocket == socket_fd:
                        self._handle_input_stream()
                    if insocket  == stopfd:
                        break
                for output in exceptready:
                    break
        except Exception as e:
            #LOG.debug(traceback.format_exc())
            LOG.debug("%s" % str(e))
            LOG.info("Offloading engine is disconnected")
        self.terminate()
        LOG.info("%s\tterminate thread" % str(self))

    def terminate(self):
        offload_engine_list.remove(self.info)
        if self.stop_file != None:
            os.write(self.stop_file.fileno(), "stop\n")
            self.stop_file.flush()
            self.stop_file.close()
            self.stop_file = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _handle_input_stream(self):
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)


class ResultMonitorServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
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
        
        if self.socket is not None:
            self.socket.close()
        LOG.info("[TERMINATE] Finish ucomm server")


def main():
    exit_status = 1
    monitor_server = None
    monitor_server = ResultMonitorServer(Const.OFFLOADING_MONITOR_PORT, ResultMonitorHandler)
    monitor_thread = threading.Thread(target=monitor_server.serve_forever)
    monitor_thread.daemon = True
    try:
        monitor_thread.start()
        while True:
            time.sleep(100)
    except Exception as e:
        sys.stderr.write(str(e))
        exit_status = 1
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        exit_status = 0
    finally:
        if monitor_server is not None:
            monitor_server.terminate()
    return exit_status


if __name__ == '__main__':
    ret = main()
    sys.exit(ret)
