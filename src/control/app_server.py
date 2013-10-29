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

import Queue
import sys
import time
import json
from config import Const as Const
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn
import SocketServer
import socket
import select
import traceback
import struct

import mobile_server
import log as logging
from protocol import Protocol_application


LOG = logging.getLogger(__name__)


class AppServerError(Exception):
    pass


class VideoSensorHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(SocketServer.StreamRequestHandler, self).setup()
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        self.result_queue = Queue.Queue()
        mobile_server.image_queue_list.append(self.data_queue)
        mobile_server.result_queue_list.append(self.result_queue)

    def _handle_video_streaming(self):
        if self.data_queue.empty() is False:
            jpeg_data = self.data_queue.get()
            LOG.info("sending new image")
            json_header = json.dumps({
                Protocol_application.JSON_KEY_SENSOR_TYPE :
                        Protocol_application.JSON_VALUE_SENSOR_TYPE_JPEG,
                })
            header_size = struct.pack("!I", len(json_header))
            image_size = struct.pack("!I", len(jpeg_data))
            self.request.send(header_size)
            self.request.send(image_size)
            self.wfile.write(json_header)
            self.wfile.write(jpeg_data)
            self.wfile.flush()

    def _handle_result_output(self):
        ret_size = self.request.recv(4)
        ret_size = struct.unpack("!I", ret_size)[0]
        result_data = self.request.recv(ret_size)
        while len(result_data) < result_data:
            result_data += self.request.recv(ret_size - len(ret_size))
        LOG.info("receive result : %s" % (str(result_data)))
        self.result_queue.put(str(result_data))

    def handle(self):
        try:
            LOG.info("new AppVM is connected")
            socket_fd = self.request.fileno()
            input_list = [socket_fd]
            output_list = [socket_fd]
            while True:
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, [])
                for s in inputready:
                    if s == socket_fd:
                        self._handle_result_output()
                for output in outputready:
                    if output == socket_fd:
                        self._handle_video_streaming()
                time.sleep(0.001)
        except Exception as e:
            sys.stderr.write(traceback.format_exc())
            sys.stderr.write("%s" % str(e))
            sys.stderr.write("handler raises exception\n")
            self.terminate()

    def terminate(self):
        mobile_server.image_queue_list.remove(self.data_queue)
        mobile_server.result_queue_list.remove(self.result_queue)


class VideoSensorServer(ThreadingMixIn, HTTPServer):
    def __init__(self, args):
        server_address = ('0.0.0.0', Const.VIDEO_PORT)
        self.allow_reuse_address = True
        try:
            SocketServer.TCPServer.__init__(self, server_address,
                    VideoSensorHandler)
        except socket.error as e:
            sys.stderr.write(str(e))
            sys.stderr.write("Check IP/Port : %s\n" % (str(server_address)))
            sys.exit(1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        LOG.info("* Video Sensor server configuration")
        LOG.info(" - Open TCP Server at %s" % (str(server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(
                    socket.IPPROTO_TCP,
                    socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def handle_error(self, request, client_address):
        pass

    def terminate(self):
        pass
