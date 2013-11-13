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

from upnp_server import UPnPServer, UPnPError
from RESTServer_binder import RESTServer, RESTServerError
import mobile_server
import log as logging
from protocol import Protocol_application
from protocol import Protocol_client


LOG = logging.getLogger(__name__)


class AppServerError(Exception):
    pass


class VideoSensorHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        #super(SocketServer.StreamRequestHandler, self).setup()
        super(VideoSensorHandler, self).setup()
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        self.result_queue = Queue.Queue()
        mobile_server.image_queue_list.append(self.data_queue)
        mobile_server.result_queue_list.append(self.result_queue)

        self.frame_latency_dict = dict()
        self.average_frame_latency = 0.0
        self.counter_frame_latency = 0

    def _handle_video_streaming(self):
        if self.data_queue.empty() is False:
            (header, jpeg_data) = self.data_queue.get()
            header = json.loads(header)
            header.update({
                Protocol_application.JSON_KEY_SENSOR_TYPE:
                        Protocol_application.JSON_VALUE_SENSOR_TYPE_JPEG,
                })

            #LOG.info("sending new image")
            json_header = json.dumps(header)
            packet = struct.pack("!II%ds%ds" % (len(json_header), len(jpeg_data)), 
                    len(json_header),
                    len(jpeg_data),
                    json_header,
                    str(jpeg_data))
            self.request.send(packet)
            self.wfile.flush()

            # latency measurement
            frame_id = header.get(Protocol_client.FRAME_MESSAGE_KEY, None)
            if frame_id is not None:
                self.frame_latency_dict[frame_id] = time.time()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppServerError("Socket is closed")
            data += tmp_data
        return data

    def _handle_result_output(self):
        ret_size = self._recv_all(4)
        ret_size = struct.unpack("!I", ret_size)[0]
        result_data = self._recv_all(ret_size)
        result_json = json.loads(result_data)
        frame_id = result_json.get(Protocol_client.FRAME_MESSAGE_KEY, None)
        if frame_id is not None:
            sending_time = self.frame_latency_dict.get(frame_id)
            if sending_time is not None:
                time_diff = time.time() - sending_time
                self.average_frame_latency += time_diff
                self.counter_frame_latency += 1
                if self.counter_frame_latency%100 == 0:
                    LOG.info("average frame latency : %f" %
                            (self.average_frame_latency /
                            self.counter_frame_latency))
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
            LOG.debug(traceback.format_exc())
            LOG.debug("%s" % str(e))
            LOG.debug("handler raises exception\n")
            LOG.info("Client is disconnected")
            self.terminate()

    def terminate(self):
        mobile_server.image_queue_list.remove(self.data_queue)
        mobile_server.result_queue_list.remove(self.result_queue)


class VideoSensorServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    stopped = False

    def __init__(self, args):
        server_address = ('0.0.0.0', Const.VIDEO_PORT)
        self.allow_reuse_address = True
        try:
            SocketServer.TCPServer.__init__(self, server_address, VideoSensorHandler)
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

        # start REST server for meta info
        try:
            self.rest_server = RESTServer()
            self.rest_server.start()
        except RESTServerError as e:
            LOG.warning(str(e))
            LOG.warning("Cannot start REST API Server")
            self.rest_server = None
        LOG.info("Start RESTful API Server")

        # Start UPnP Server
        try:
            self.upnp_server = UPnPServer()
            self.upnp_server.start()
        except UPnPError as e:
            LOG.warning(str(e))
            LOG.warning("Cannot start UPnP Server")
            self.upnp_server = None
        LOG.info("Start UPnP Server")

    def serve_forever(self):
        while not self.stopped:
            self.handle_request()

    def handle_error(self, request, client_address):
        import pdb;pdb.set_trace()
        LOG.info("error")
        pass

    def terminate(self):
        self.server_close()
        self.stopped = True
        
        if self.socket != -1:
            self.socket.close()
        if hasattr(self, 'upnp_server') and self.upnp_server is not None:
            LOG.info("[TERMINATE] Terminate UPnP Server")
            self.upnp_server.terminate()
            self.upnp_server.join()
        if hasattr(self, 'rest_server') and self.rest_server is not None:
            LOG.info("[TERMINATE] Terminate REST API monitor")
            self.rest_server.terminate()
            self.rest_server.join()
        LOG.info("[TERMINATE] Finish app communication server connection")
