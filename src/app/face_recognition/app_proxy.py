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

import traceback
import threading
import socket
import select
import struct
import json
import sys
import Queue
import time

from protocol import Protocol_client
import log as logging


LOG = logging.getLogger(__name__)


class AppProxyError(Exception):
    pass

class AppProxyStreamingClient(threading.Thread):
    """
    This client will receive data from the control server as much as possible.
    And put the data into the queue, so that other thread can use the image
    """

    def __init__(self, control_addr, image_queue, result_queue):
        self.stop = threading.Event()
        self.port_number = None
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.connect(control_addr)
        except socket.error as e:
            raise AppProxyError("Failed to connect to %s" % str(control_addr))
        self.image_queue = image_queue
        self.result_queue = result_queue
        threading.Thread.__init__(self, target=self.streaming)

    def streaming(self):
        LOG.info("Start getting data from the server")
        socket_fd = self.sock.fileno()
        input_list = [socket_fd]
        output_list = [socket_fd]
        try:
            while(not self.stop.wait(0.01)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, [])
                for s in inputready:
                    if s == socket_fd:
                        self._handle_video_streaming()
                for s in outputready:
                    if s == socket_fd:
                        self._handle_result_output()
        except Exception as e:
            LOG.warning(traceback.format_exc())
            LOG.warning("%s" % str(e))
            LOG.warning("handler raises exception")
            LOG.warning("Server is disconnected unexpectedly")
        self.sock.close()

    def terminate(self):
        self.stop.set()
        if self.sock is not None:
            self.sock.close()

    def _recv_all(self, sock, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = sock.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def _handle_video_streaming(self):
        # receive data from control VM
        #LOG.info("receiving new image\n")
        header_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        data_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        header = self._recv_all(self.sock, header_size)
        data = self._recv_all(self.sock, data_size)
        header_data = json.loads(header)
        if self.image_queue.full() is True:
            self.image_queue.get()
        self.image_queue.put((header_data, data))

    def _handle_result_output(self):
        while self.result_queue.empty() is False:
            return_data = self.result_queue.get()
            packet = struct.pack("!I%ds" % len(return_data),
                    len(return_data), return_data)
            self.sock.sendall(packet)
            LOG.info("returning result: %s" % return_data)


class AppProxyBlockingClient(threading.Thread):
    def __init__(self, control_addr):
        self.stop = threading.Event()
        self.port_number = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect(control_addr)
        self.result_queue = Queue.Queue()
        threading.Thread.__init__(self, target=self.block_read)

    def block_read(self):
        LOG.info("Start getting data from the server")
        socket_fd = self.sock.fileno()
        input_list = [socket_fd]
        output_list = [socket_fd]
        try:
            while(not self.stop.wait(0.01)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, [])
                for s in inputready:
                    if s == socket_fd:
                        self._handle_video_streaming()
                for s in outputready:
                    if s == socket_fd:
                        self._handle_result_output()
        except Exception as e:
            LOG.info(traceback.format_exc())
            LOG.info("%s" % str(e))
            LOG.info("handler raises exception")
            LOG.info("Server is disconnected unexpectedly")
        self.sock.close()

    def terminate(self):
        self.stop.set()
        if self.sock is not None:
            self.sock.close()

    def _recv_all(self, sock, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = sock.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def handle_frame(self, data):
        pass

    def _handle_video_streaming(self):
        # receive data from control VM
        #LOG.info("receiving new image\n")
        header_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        data_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        header = self._recv_all(self.sock, header_size)
        data = self._recv_all(self.sock, data_size)
        header_data = json.loads(header)
        frame_id = header_data.get(Protocol_client.FRAME_MESSAGE_KEY, None)

        return_message = self.handle_frame(data)
        if return_message is None:
            return_message = dict()

        if frame_id is not None:
            return_message[Protocol_client.FRAME_MESSAGE_KEY] = frame_id

        if len(return_message) > 0:
            self.result_queue.put(json.dumps(return_message))

    def _handle_result_output(self):
        while self.result_queue.empty() is False:
            return_data = self.result_queue.get()
            packet = struct.pack("!I%ds" % len(return_data),
                    len(return_data), return_data)
            self.sock.sendall(packet)
            LOG.info("returning result: %s" % return_data)


class AppProxyThread(threading.Thread):
    def __init__(self, image_queue, output_queue):
        self.image_queue = image_queue
        self.output_queue = output_queue
        self.stop = threading.Event()
        threading.Thread.__init__(self, target=self.run)

    def run(self):
        while(not self.stop.wait(0.001)):
            try:
                (header, data) = self.image_queue.get_nowait()
            except Queue.Empty as e:
                time.sleep(0.001)
                continue
            if header == None or data == None:
                time.sleep(0.001)
                continue

            return_message = dict()
            result = self.handle(header, data)
            if result is not None:
                return_message[Protocol_client.RESULT_MESSAGE_KEY] = result
            frame_id = header.get(Protocol_client.FRAME_MESSAGE_KEY, None)
            if frame_id is not None:
                return_message[Protocol_client.FRAME_MESSAGE_KEY] = frame_id

            self.output_queue.put(json.dumps(return_message))

    def handle(self, header, data):
        return None

    def terminate(self):
        self.stop.set()


