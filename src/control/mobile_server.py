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

from config import Const as Const
import log as logging
from protocol import Protocol_client


LOG = logging.getLogger(__name__)
image_queue_list = list()
result_queue_list = list()


class MobileCommError(Exception):
    pass

class MobileSensorHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(MobileVideoHandler, self).setup()
        self.stop = threading.Event()
        self.average_FPS = 0.0
        self.current_FPS = 0.0
        self.init_connect_time = None
        self.previous_time = None
        self.current_time = 0
        self.frame_count = 0
        self.ret_frame_ids = Queue.Queue()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise MobileCommError("Socket is closed")
            data += tmp_data
        return data

    def handle(self):
        global image_queue_list
        try:
            LOG.info("new Google Glass is connected")
            self.init_connect_time = time.time()
            self.previous_time = time.time()

            socket_fd = self.request.fileno()
            input_list = [socket_fd]
            output_list = [socket_fd]
            except_list = [socket_fd]

            while(not self.stop.wait(0.0001)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, except_list)
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
            LOG.info(traceback.format_exc())
            LOG.info("%s" % str(e))
            LOG.info("handler raises exception\n")
            LOG.info("Client disconnected")
        if self.socket != -1:
            self.socket.close()

    def terminate(self):
        self.stop.set()


class MobileVideoHandler(MobileSensorHandler):
    def setup(self):
        super(MobileVideoHandler, self).setup()

    def _handle_input_data(self):
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        img_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        image_data = self._recv_all(img_size)
        self.frame_count += 1

        # measurement
        self.current_time = time.time()
        self.current_FPS = 1 / (self.current_time - self.previous_time)
        self.average_FPS = self.frame_count / (self.current_time -
                self.init_connect_time)
        self.previous_time = self.current_time

        if (self.frame_count % 100 == 0):
            msg = "Video frame rate from client : current(%f), average(%f)" % \
                    (self.current_FPS, self.average_FPS)
            LOG.info(msg)
        for image_queue in image_queue_list:
            if image_queue.full() is True:
                image_queue.get()
            image_queue.put((header_data, image_data))

        # return frame id to flow control
        json_header = json.loads(header_data)
        frame_id = json_header.get(Protocol_client.FRAME_MESSAGE_KEY, None)
        if frame_id is not None:
            self.ret_frame_ids.put(frame_id)

    def _handle_output_result(self):
        global result_queue_list
        def _post_header_process(header_message):
            header_json = json.loads(header_message)
            frame_id = header_json.get(Protocol_client.FRAME_MESSAGE_KEY, None)
            if frame_id is not None:
                header_json[Protocol_client.RESULT_ID_MESSAGE_KEY] = frame_id
                del header_json[Protocol_client.FRAME_MESSAGE_KEY]
            return json.dumps(frame_id)

        # return result from the token bucket
        try:
            return_frame_id = self.ret_frame_ids.get_nowait()
            return_data = json.dumps({
                    Protocol_client.FRAME_MESSAGE_KEY: return_frame_id,
                    })
            packet = struct.pack("!I%ds" % len(return_data),
                    len(return_data),
                    return_data)
            self.request.send(packet)
            self.wfile.flush()
        except Queue.Empty:
            pass

        # return result from the application server
        for result_queue in result_queue_list:
            result_msg = None
            try:
                result_msg = result_queue.get_nowait()
            except Queue.Empty:
                pass
            if result_msg is not None:
                # process header a little bit since we like to differenciate
                # frame id that comes from an application with the frame id for
                # the token bucket.
                processed_result_msg = _post_header_process(result_msg)
                packet = struct.pack("!I%ds" % len(processed_result_msg),
                        len(processed_result_msg),
                        processed_result_msg)
                self.request.send(packet)
                self.wfile.flush()

                # send only one
                LOG.info("result message (%s) sent to the Glass", result_msg)
                break


class MobileAccHandler(MobileSensorHandler):
    def setup(self):
        super(MobileVideoHandler, self).setup()

    def _handle_input_data(self):
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        acc_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        acc_data = self._recv_all(acc_size)
        self.frame_count += 1

        # measurement
        self.current_time = time.time()
        self.current_FPS = 1 / (self.current_time - self.previous_time)
        self.average_FPS = self.frame_count / (self.current_time -
                self.init_connect_time)
        self.previous_time = self.current_time

        if (self.frame_count % 100 == 0):
            msg = "ACC rate from client : current(%f), average(%f)" % \
                    (self.current_FPS, self.average_FPS)
            LOG.info(msg)
        for image_queue in image_queue_list:
            if image_queue.full() is True:
                image_queue.get()
            image_queue.put((header_data, image_data))

        # return frame id to flow control
        #json_header = json.loads(header_data)
        #frame_id = json_header.get(Protocol_client.FRAME_MESSAGE_KEY, None)
        #if frame_id is not None:
        #    self.ret_frame_ids.put(frame_id)

    def _handle_output_result(self):
        pass
        '''
        try:
            return_frame_id = self.ret_frame_ids.get_nowait()
            return_data = json.dumps({
                    Protocol_client.FRAME_MESSAGE_KEY: return_frame_id,
                    })
            packet = struct.pack("!I%ds" % len(return_data),
                    len(return_data),
                    return_data)
            self.request.send(packet)
            self.wfile.flush()
        except Queue.Empty:
            pass
        '''


class MobileCommServer(SocketServer.TCPServer): 
    stopped = False

    def __init__(self, port, handler):
        server_address = ('0.0.0.0', port)
        self.allow_reuse_address = True
        try:
            SocketServer.TCPServer.__init__(self, server_address,
                    handler)
        except socket.error as e:
            sys.stderr.write(str(e))
            sys.stderr.write("Check IP/Port : %s\n" % (str(server_address)))
            sys.exit(1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        LOG.info("* Mobile server configuration")
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
        #SocketServer.TCPServer.handle_error(self, request, client_address)
        #sys.stderr.write("handling error from client")
        pass

    def terminate(self):
        self.server_close()
        self.stopped = True

        # close all thread
        if self.socket != -1:
            self.socket.close()
        LOG.info("[TERMINATE] Finish mobile communication server connection")


def main():
    video_server = MobileCommServer(Const.MOBILE_SERVER_VIDEO_PORT, MobileVideoHandler)
    acc_server = MobileCommServer(Const.MOBILE_SERVER_ACC_PORT, MobileVideoHandler)

    video_thread = threading.Thread(target=video_server.serve_forever)
    acc_thread = threading.Thread(target=acc_server.serve_forever)
    video_thread.daemon = True
    acc_thread.daemon = True

    try:
        video_thread.start()
        acc_thread.start()
    except Exception as e:
        sys.stderr.write(str(e))
        video_server.terminate()
        acc_server.terminate()
        sys.exit(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        video_server.terminate()
        acc_server.terminate()
        sys.exit(1)
    else:
        video_server.terminate()
        acc_server.terminate()
        sys.exit(0)


if __name__ == '__main__':
    main()
