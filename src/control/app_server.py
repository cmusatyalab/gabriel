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
import os
import multiprocessing
import tempfile
from config import Const as Const
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import SocketServer
import socket
import select
import traceback
import struct

import mobile_server
import log as logging
import threading
from protocol import Protocol_application
from protocol import Protocol_client


LOG = logging.getLogger(__name__)


class AppServerError(Exception):
    pass


class SensorHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(SensorHandler, self).setup()
        self.stop_queue = multiprocessing.Queue()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data is None or len(tmp_data) is 0:
                raise AppServerError("Socket is closed")
            data += tmp_data
        return data

    def _handle_input_data(self):
        """ No input expected.
        But blocked read will return 0 if the other side closed gracefully
        """
        ret_data = self.request.recv(1)
        if ret_data is None:
            raise AppServerError("Cannot recv data at %s" % str(self))
        if len(ret_data) == 0:
            raise AppServerError("Client side is closed gracefullu at %s" % str(self))

    def handle(self):
        try:
            LOG.info("Offloading engine is connected")
            socket_fd = self.request.fileno()
            stopfd = self.stop_queue._reader.fileno()
            input_list = [socket_fd, stopfd]
            output_list = [socket_fd, stopfd]
            except_list = [socket_fd, stopfd]
            is_running = True
            while is_running:
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, except_list)
                for s in inputready:
                    if s == socket_fd:
                        self._handle_input_data()
                    if s == stopfd:
                        is_running = False;
                for output in outputready:
                    if output == socket_fd:
                        self._handle_sensor_stream()
                    if output == stopfd:
                        is_running = False;
                for output in exceptready:
                    is_running = False;
        except Exception as e:
            LOG.debug(traceback.format_exc())
            LOG.debug("%s" % str(e))

        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.stop_queue is not None:
            self.stop_queue.close()
            self.stop_queue = None
        LOG.info("%s\tterminate thread" % str(self))

    def terminate(self):
        self.stop_queue.put("terminate\n")


class VideoSensorHandler(SensorHandler):
    def setup(self):
        super(VideoSensorHandler, self).setup()
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        mobile_server.image_queue_list.append(self.data_queue)

    def _handle_sensor_stream(self):
        try:
            (header, jpeg_data) = self.data_queue.get_nowait()
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
        except Queue.Empty as e:
            pass

    def terminate(self):
        LOG.info("Video Offloading Engine is disconnected")
        mobile_server.image_queue_list.remove(self.data_queue)
        super(VideoSensorHandler, self).setup()


class AccSensorHandler(SensorHandler):
    def setup(self):
        super(AccSensorHandler, self).setup()
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        mobile_server.acc_queue_list.append(self.data_queue)

    def _handle_sensor_stream(self):
        try:
            (header, acc_data) = self.data_queue.get_nowait()
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
        except Queue.Empty as e:
            pass

    def terminate(self):
        mobile_server.acc_queue_list.remove(self.data_queue)
        super(AccSensorHandler, self).setup()


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
        
        if self.socket is not None:
            self.socket.close()
        LOG.info("[TERMINATE] Finish app communication server connection")


class OffloadingEngineMonitor(threading.Thread):
    def __init__(self, v_queuelist, a_queuelist, g_queuelist, result_queue):
        self.stop = threading.Event()
        threading.Thread.__init__(self, target=self.monitor)
        self.v_queuelist = v_queuelist
        self.a_queuelist = a_queuelist
        self.g_queuelist = g_queuelist
        self.result_queue = result_queue

        self.count_prev_video_app = 0
        self.count_prev_acc_app = 0
        self.count_prev_gps_app = 0
        self.count_cur_video_app = 0
        self.count_cur_acc_app = 0
        self.count_cur_gps_app = 0

    def _inject_token(self):
        '''
        if self.result_queue.empty() == True:
            LOG.info("Inject token to start receiving data from the Glass")
            header = json.dumps({
                Protocol_client.TOKEN_INJECT_KEY: int(Const.TOKEN_INJECTION_SIZE),
                })
            self.result_queue.put(header)
        '''
        pass

    def monitor(self):
        while(not self.stop.wait(0.01)):
            self.count_cur_video_app = len(self.v_queuelist)
            self.count_cur_acc_app = len(self.a_queuelist)
            self.count_cur_gps_app = len(self.g_queuelist)

            if (self.count_prev_video_app == 0 and self.count_cur_video_app > 0) or \
                    (self.count_prev_acc_app == 0 and self.count_cur_acc_app > 0) or \
                    (self.count_prev_gps_app == 0 and self.count_cur_gps_app > 0):
                self._inject_token()
            self.count_prev_video_app = self.count_cur_video_app
            self.count_prev_acc_app = self.count_cur_acc_app
            self.count_prev_gps_app = self.count_cur_gps_app

    def terminate(self):
        self.stop.set()

