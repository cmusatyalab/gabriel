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
import tempfile
import os

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
acc_queue_list = list()
gps_queue_list = list()
result_queue = Queue.Queue()


class MobileCommError(Exception):
    pass

class MobileSensorHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(MobileSensorHandler, self).setup()
        self.average_FPS = 0.0
        self.current_FPS = 0.0
        self.init_connect_time = None
        self.previous_time = None
        self.current_time = 0
        self.frame_count = 0
        self.totoal_recv_size = 0
        self.ret_frame_ids = Queue.Queue()
        self.stop_file= tempfile.TemporaryFile(prefix="gabriel-threadfd-")

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise MobileCommError("Cannot recv data at %s" % str(self))
            data += tmp_data
        return data

    def handle(self):
        global image_queue_list
        try:
            LOG.info("Google Glass is connected for (%s)" % str(self))
            self.init_connect_time = time.time()
            self.previous_time = time.time()

            socket_fd = self.request.fileno()
            stopfd = self.stop_file.fileno()
            input_list = [socket_fd, stopfd]
            output_list = [socket_fd, stopfd]
            except_list = [socket_fd, stopfd]

            while True:
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, except_list)
                for s in inputready:
                    if s == socket_fd:
                        self._handle_input_data()
                    if s == stopfd:
                        break
                for output in outputready:
                    if output == socket_fd:
                        self._handle_output_result()
                    if s == stopfd:
                        break
                for e in exceptready:
                    if s == stopfd:
                        break
                    if e == socket_fd:
                        break

                if not (inputready or outputready or exceptready):
                    continue
                time.sleep(0.0001)
        except Exception as e:
            #LOG.info(traceback.format_exc())
            LOG.debug("%s\n" % str(e))
            pass
        self.terminate()
        LOG.info("%s\tterminate thread" % str(self))
    def _handle_input_data(self):
        pass

    def _handle_output_result(self):
        pass

    def terminate(self):
        os.write(self.stop_file.fileno(), "stop\n")
        self.stop_file.flush()
        if self.connection is not None:
            self.connection.close()
            self.connection = None
            self.stop_file.close()
            self.stop_file = None


class MobileVideoHandler(MobileSensorHandler):
    def setup(self):
        super(MobileVideoHandler, self).setup() 
        
    def __repr__(self):
        return "Mobile Video Server"

    def _handle_input_data(self):
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        img_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        image_data = self._recv_all(img_size)
        self.frame_count += 1

        # measurement
        self.current_time = time.time()
        self.totoal_recv_size += (header_size + img_size + 8)
        self.current_FPS = 1 / (self.current_time - self.previous_time)
        self.average_FPS = self.frame_count / (self.current_time -
                self.init_connect_time)
        self.previous_time = self.current_time

        if (self.frame_count % 100 == 0):
            msg = "Video FPS : current(%f), avg(%f), BW(%f Mbps), offloading engine(%d)" % \
                    (self.current_FPS, self.average_FPS, \
                    8*self.totoal_recv_size/(self.current_time-self.init_connect_time)/1000/1000,
                    len(image_queue_list))
            LOG.info(msg)
        for image_queue in image_queue_list:
            if image_queue.full() is True:
                image_queue.get()
            image_queue.put((header_data, image_data))

        # return frame id to flow control
        '''
        json_header = json.loads(header_data)
        frame_id = json_header.get(Protocol_client.FRAME_MESSAGE_KEY, None)
        if frame_id is not None:
            self.ret_frame_ids.put(frame_id)
        global result_queue
        result_queue.put(header_data)
        '''


class MobileAccHandler(MobileSensorHandler):
    def setup(self):
        super(MobileAccHandler, self).setup()
        
    def __str__(self):
        return "Mobile Acc Server"

    def __repr__(self):
        return "Mobile Acc Server"

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
            msg = "ACC FPS : current(%f), average(%f), offloading Engine(%d)" % \
                    (self.current_FPS, self.average_FPS, len(acc_queue_list))
            LOG.info(msg)

        try:
            for acc_queue in acc_queue_list:
                if acc_queue.full() is True:
                    acc_queue.get_nowait()
                acc_queue.put_nowait((header_data, acc_data))
        except Queue.Empty as e:
            pass
        except Queue.Full as e:
            pass

    def _handle_output_result(self):
        """ control message
        """
        pass


class MobileResultHandler(MobileSensorHandler):
    def setup(self):
        super(MobileResultHandler, self).setup()

    def __str__(self):
        return "Mobile Result Handler"

    def __repr__(self):
        return "Mobile Result Handler"

    def _handle_input_data(self):
        """ No input expected.
        But blocked read will return 0 if the other side closed gracefully
        """
        ret_data = self.request.recv(1)
        if ret_data == None:
            raise MobileCommError("Cannot recv data at %s" % str(self))
        if len(ret_data) == 0:
            raise MobileCommError("Client side is closed gracefullu at %s" % str(self))

    def _handle_output_result(self):
        global result_queue

        def _post_header_process(header_message):
            header_json = json.loads(header_message)
            frame_id = header_json.get(Protocol_client.FRAME_MESSAGE_KEY, None)
            if frame_id is not None:
                header_json[Protocol_client.RESULT_ID_MESSAGE_KEY] = frame_id
                del header_json[Protocol_client.FRAME_MESSAGE_KEY]
            return json.dumps(frame_id)

        if result_queue.empty() is False:
            result_msg = None
            try:
                result_msg = result_queue.get_nowait()
                # process header a little bit since we like to differenciate
                # frame id that comes from an application with the frame id for
                # the token bucket.
                packet = struct.pack("!I%ds" % len(result_msg),
                        len(result_msg),
                        result_msg)
                self.request.send(packet)
                self.wfile.flush()

                # send only one
                LOG.info("result message (%s) sent to the Glass", result_msg)
            except Queue.Empty:
                pass


class MobileCommServer(SocketServer.TCPServer):
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
        LOG.info("* Mobile server(%s) configuration" % str(self.handler))
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
        if self.socket is not None:
            self.socket.close()
        LOG.info("[TERMINATE] Finish %s" % str(self.handler))


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
