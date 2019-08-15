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
import select
import socket

import sys
if (sys.version_info > (3, 0)):
    import socketserver
else:
    import SocketServer as socketserver

import threading
import traceback

import gabriel
LOG = gabriel.logging.getLogger(__name__)


class TCPNetworkError(Exception):
    pass

class TCPZeroBytesError(Exception):
    pass

class CommonHandler(socketserver.StreamRequestHandler, object):
    '''
    A basic handler to be used with TCP server.
    A real handler can extend this class by implementing interesting stuff in
        _handle_input_data, which is triggered by input transmission, or
        _handle_queue_data, which is triggered by putting anything in self.data_queue
    '''
    def setup(self):
        super(CommonHandler, self).setup()
        self.stop_queue = multiprocessing.Queue()

    def _recv_all(self, recv_size):
        '''
        Received data till a specified size.
        '''
        chunks = []
        bytes_recd = 0
        while bytes_recd < recv_size:
            chunk = self.request.recv(recv_size - bytes_recd)
            if chunk is None:
                raise TCPNetworkError("Cannot recv data at %s" % str(self))
            if chunk == b'':
                raise TCPZeroBytesError("Recv 0 bytes.")
            chunks.append(chunk)
            bytes_recd = bytes_recd + len(chunk)
        return b''.join(chunks)

    def handle(self):
        try:
            ## input list
            # 1) react whenever there's input data from client
            # 2) (optional) a data queue may trigger some processing
            # 3) a stop queue to notify termination
            socket_fd = self.request.fileno()
            stop_fd = self.stop_queue._reader.fileno()
            input_list = [socket_fd, stop_fd]
            data_queue_fd = -1
            if hasattr(self, 'data_queue'):
                data_queue_fd = self.data_queue._reader.fileno()
                input_list += [data_queue_fd]

            ## except list
            except_list = [socket_fd, stop_fd]

            is_running = True
            while is_running:
                inputready, outputready, exceptready = select.select(input_list, [], except_list)
                for s in inputready:
                    if s == socket_fd:
                        self._handle_input_data()
                    if s == stop_fd:
                        is_running = False
                    # For output, check queue first. If we check output socket,
                    # select may return immediately (in case when nothing is sent out)
                    if s == data_queue_fd:
                        self._handle_queue_data()
                for e in exceptready:
                    is_running = False
        except TCPZeroBytesError as e:
            LOG.info("Connection closed (%s)" % str(self))
        except Exception as e:
            LOG.warning("connection closed not gracefully (%s): %s\n" % (str(self), str(e)))
            LOG.warning(traceback.format_exc())

        if self.connection is not None:
            self.connection.close()
            self.connection = None
        LOG.info("[TERMINATE] Finish %s" % str(self))

    def _handle_input_data(self):
        """
        By default, no input is expected.
        But blocked read will return 0 if the other side closes gracefully
        """
        data = self.request.recv(1)
        if data is None:
            raise TCPNetworkError("Cannot recv data at %s" % str(self))
        if len(data) == 0:
            raise TCPZeroBytesError("Recv 0 bytes.")
        else:
            LOG.error("unexpected network input in %s" % str(self))
            self.terminate()

    def _handle_queue_data(self):
        pass

    def terminate(self):
        self.stop_queue.put("terminate\n")


class CommonServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
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
            socketserver.TCPServer.__init__(self, self.server_address, handler)
        except socket.error as e:
            LOG.error("socket error: %s" % str(e))
            raise TCPNetworkError("Check IP/Port : %s\n" % (str(self.server_address)))
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def serve_forever(self):
        while self.is_running:
            self.handle_request()

    def handle_error(self, request, client_address):
        #socketserver.TCPServer.handle_error(self, request, client_address)
        LOG.warning("Exception raised in handling request!")

    def terminate(self):
        self.server_close()
        self.is_running = False

        # close all threads
        if self.socket is not None:
            self.socket.close()
        LOG.info("[TERMINATE] Finish server with handler %s" % str(self.handler))


class CommonClient(threading.Thread):
    """
    A basic TCP client that connects to the server at @server_address.
    A real client can extend this class by implementing interesting stuff in
        _handle_input_data, which is triggered by input transmission, or
        _handle_queue_data, which is triggered by putting anything in self.data_queue
    """
    def __init__(self, server_address):
        self.server_address = server_address
        # set up socket connection to the server
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect(server_address)

        self.stop_queue = multiprocessing.Queue()
        threading.Thread.__init__(self, target = self.run)

    def _recv_all(self, recv_size):
        '''
        Received data till a specified size.
        '''
        data = ''
        while len(data) < recv_size:
            tmp_data = self.sock.recv(recv_size - len(data))
            if tmp_data is None:
                raise TCPNetworkError("Cannot recv data at %s" % str(self))
            if len(tmp_data) == 0:
                raise TCPZeroBytesError("Recv 0 bytes.")
            data += tmp_data
        return data

    def run(self):
        try:
            ## input list
            # 1) react whenever there's input data from client
            # 2) (optional) a data queue may trigger some processing
            # 3) a stop queue to notify termination
            socket_fd = self.sock.fileno()
            stop_fd = self.stop_queue._reader.fileno()
            input_list = [socket_fd, stop_fd]
            data_queue_fd = -1
            if hasattr(self, 'data_queue'):
                data_queue_fd = self.data_queue._reader.fileno()
                input_list += [data_queue_fd]

            ## except list
            except_list = [socket_fd, stop_fd]

            is_running = True
            while is_running:
                inputready, outputready, exceptready = \
                        select.select(input_list, [], except_list)
                for s in inputready:
                    if s == socket_fd:
                        self._handle_input_data()
                    if s == stop_fd:
                        is_running = False
                    # For output, check queue first. If we check output socket,
                    # select may return immediately (in case when nothing is sent out)
                    if s == data_queue_fd:
                        self._handle_queue_data()
                for e in exceptready:
                    is_running = False
        except TCPZeroBytesError as e:
            LOG.info("Connection to (%s) closed: %s\n" % (self.server_address, str(e)))
        except Exception as e:
            LOG.warning("Connection to (%s) closed not gracefully: %s\n" % (self.server_address, str(e)))
            LOG.warning(traceback.format_exc())

        if self.sock is not None:
            self.sock.close()
            self.sock = None
        LOG.info("[TERMINATE] Finish %s" % str(self))

    def _handle_input_data(self):
        """
        By default, no input is expected.
        But blocked read will return 0 if the other side closes gracefully
        """
        data = self.sock.recv(1)
        if data is None:
            raise TCPNetworkError("Cannot recv data at %s" % str(self))
        if len(data) == 0:
            raise TCPZeroBytesError("Recv 0 bytes.")
        else:
            LOG.error("unexpected network input in %s" % str(self))
            self.terminate()

    def _handle_queue_data(self):
        pass

    def terminate(self):
        self.stop_queue.put("terminate\n")
