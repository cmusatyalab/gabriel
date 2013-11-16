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
import SocketServer
import socket
import select
import traceback
import struct

import mobile_server
import log as logging
from protocol import Protocol_application
from protocol import Protocol_client


LOG = logging.getLogger(__name__)


class AppServerError(Exception):
    pass


class SensorHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(SensorHandler, self).setup()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppServerError("Socket is closed")
            data += tmp_data
        return data

    def handle(self):
        try:
            LOG.info("new AppVM is connected")
            socket_fd = self.request.fileno()
            output_list = [socket_fd]
            while True:
                inputready, outputready, exceptready = \
                        select.select([], output_list, [], 0)
                for output in outputready:
                    if output == socket_fd:
                        self._handle_input_stream()
                time.sleep(0.001)
        except Exception as e:
            LOG.debug(traceback.format_exc())
            LOG.debug("%s" % str(e))
            LOG.debug("handler raises exception\n")
            LOG.info("AppVM is disconnected")
            self.terminate()


class VideoSensorHandler(SensorHandler):
    def setup(self):
        super(VideoSensorHandler, self).setup()
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        mobile_server.image_queue_list.append(self.data_queue)

    def _handle_input_stream(self):
        if self.data_queue.empty() is False:
            (header, jpeg_data) = self.data_queue.get()
            header = json.loads(header)
            header.update({
                Protocol_application.JSON_KEY_SENSOR_TYPE:
                        Protocol_application.JSON_VALUE_SENSOR_TYPE_JPEG,
                })

            json_header = json.dumps(header)
            packet = struct.pack("!II%ds%ds" % (len(json_header), len(jpeg_data)), 
                    len(json_header),
                    len(jpeg_data),
                    json_header,
                    str(jpeg_data))
            self.request.send(packet)
            self.wfile.flush()

    def terminate(self):
        mobile_server.image_queue_list.remove(self.data_queue)


class AccSensorHandler(SensorHandler):
    def setup(self):
        super(AccSensorHandler, self).setup()
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        mobile_server.acc_queue_list.append(self.data_queue)

    def _handle_input_stream(self):
        if self.data_queue.empty() is False:
            (header, acc_data) = self.data_queue.get()
            header = json.loads(header)
            header.update({
                Protocol_application.JSON_KEY_SENSOR_TYPE:
                        Protocol_application.JSON_VALUE_SENSOR_TYPE_ACC,
                })

            json_header = json.dumps(header)
            packet = struct.pack("!II%ds%ds" % (len(json_header), len(acc_data)), 
                    len(json_header),
                    len(acc_data),
                    json_header,
                    str(acc_data))
            self.request.send(packet)
            self.wfile.flush()

    def terminate(self):
        mobile_server.acc_queue_list.remove(self.data_queue)


class ApplicationServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
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
        LOG.info("* Application server(%s) configuration" % str(self.handler))
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
        LOG.info("error")
        pass

    def terminate(self):
        self.server_close()
        self.stopped = True
        
        if self.socket != -1:
            self.socket.close()
        LOG.info("[TERMINATE] Finish app communication server connection")


