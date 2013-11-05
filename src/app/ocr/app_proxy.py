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
import ocr_server

from protocol import Protocol_client


class AppProxyError(Exception):
    pass

class AppProxyClient(threading.Thread):
    def __init__(self, control_addr):
        self.stop = threading.Event()
        self.port_number = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect(control_addr)
        self.result_queue = Queue.Queue()
        threading.Thread.__init__(self, target=self.streaming)

    def streaming(self):
        sys.stdout.write("Start getting data from the server\n")
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
            sys.stderr.write(traceback.format_exc())
            sys.stderr.write("%s" % str(e))
            sys.stderr.write("handler raises exception\n")
            sys.stdout.write("Server is disconnected unexpectedly\n")
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
        #sys.stdout.write("receiving new image\n")
        header_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        data_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        header = self._recv_all(self.sock, header_size)
        data = self._recv_all(self.sock, data_size)
        header_data = json.loads(header)
        frame_id = header_data.get(Protocol_client.FRAME_MESSAGE_KEY, None)

        # echo server
        result = ocr_server.run_ocr(data)
        return_message = dict()
        if frame_id is not None:
            return_message[Protocol_client.FRAME_MESSAGE_KEY] = frame_id
        if result is not None and len(result.strip()) > 0:
            return_message[Protocol_client.RESULT_MESSAGE_KEY] = result
        self.result_queue.put(json.dumps(return_message))

    def _handle_result_output(self):
        while self.result_queue.empty() is False:
            return_data = self.result_queue.get()
            packet = struct.pack("!I%ds" % len(return_data),
                    len(return_data), return_data)
            self.sock.sendall(packet)
            sys.stdout.write("returning result: %s\n" % return_data)

if __name__ == "__main__":

    sys.stdout.write("Start receiving data\n")
    control_addr = ("128.2.210.197", 10101)
    client = AppProxyClient(control_addr)
    client.start()
    client.isDaemon = True
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
        client.terminate()
    except Exception as e:
        client.terminate()

