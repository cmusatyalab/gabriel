#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Zhuo Chen <zhuoc@cs.cmu.edu>
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

import multiprocessing
import socket
import SocketServer
import sys
import time
import traceback

import gabriel
LOG = gabriel.logging.getLogger(__name__)


class NetworkError(Exception):
    pass


class CommonServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    '''
    A basic TCP server.
    It handles each TCP connection in the @handler provided to __init__.
    '''
    is_running = True

    def __init__(self, port, handler):
        self.server_address = ('0.0.0.0', port)
        self.allow_reuse_address = True
        self.handler = handler
        try:
            SocketServer.TCPServer.__init__(self, self.server_address, handler)
        except socket.error as e:
            LOG.error("socket error: %s" % str(e))
            raise NetworkError("Check IP/Port : %s\n" % (str(self.server_address)))
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def serve_forever(self):
        while self.is_running:
            self.handle_request()

    def handle_error(self, request, client_address):
        #SocketServer.TCPServer.handle_error(self, request, client_address)
        LOG.warning("handling error from client")

    def terminate(self):
        self.server_close()
        self.is_running = False

        # close all threads
        if self.socket is not None:
            self.socket.close()
        LOG.info("[TERMINATE] Finish %s" % str(self.handler))


class CommonHandler(SocketServer.StreamRequestHandler, object):
    '''
    A basic handler to be used with TCP server.
    A real handler can extend this class by implementing interesting stuff in
        _handle_input_data, which is triggered by input transimision, or
        _handle_queue_data, which is triggered by putting anything in self.data_queue
    '''
    def setup(self):
        super(CommonHandler, self).setup()
        self.stop_queue = multiprocessing.Queue()

    def _recv_all(self, recv_size):
        '''
        Received data till a specified size.
        '''
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data is None:
                raise NetworkError("Cannot recv data at %s" % str(self))
            if len(tmp_data) == 0:
                raise NetworkError("Recv 0 data at %s" % str(self))
            data += tmp_data
        return data

    def handle(self):
        try:
            ## input list
            # 1) react whenever there's input data from client
            # 2) (optional) a data queue may trigger some processing
            # 3) a stop queue to notify termination
            socket_fd = self.request.fileno()
            stopfd = self.stop_queue._reader.fileno()
            input_list = [socket_fd, stopfd]
            data_queue_fd = -1
            if hasattr(self, 'data_queue'):
                data_queue_fd = self.data_queue._reader.fileno()
                input_list += [data_queue_fd]

            ## except list
            except_list = [socket_fd, stopfd]

            is_running = True
            while is_running:
                inputready, outputready, exceptready = \
                        select.select(input_list, [], except_list)
                for s in inputready:
                    if s == socket_fd:
                        self._handle_input_data()
                    if s == stopfd:
                        is_running = False
                    # For output, check queue first. If we check output socket,
                    # select may return immediately (in case when nothing is sent out)
                    if s == data_queue_fd:
                        self._handle_queue_data()
                for e in exceptready:
                    is_running = False
        except Exception as e:
            LOG.info(traceback.format_exc())
            LOG.warning("connection closed: %s\n" % str(e))

        if self.connection is not None:
            self.connection.close()
            self.connection = None
        LOG.info("%s\tterminate thread" % str(self))

    def _handle_input_data(self):
        """
        By default, no input is expected.
        But blocked read will return 0 if the other side closes gracefully
        """
        ret_data = self.request.recv(1)
        if ret_data is None:
            raise NetworkError("Cannot recv data at %s" % str(self))
        if len(ret_data) == 0:
            LOG.info("Client side is closed gracefully at %s" % str(self))

    def _handle_queue_data(self):
        pass

    def terminate(self):
        self.stop_queue.put("terminate\n")
