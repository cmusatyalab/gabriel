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

import threading
import socket
import select
import struct
import json
import sys
import Queue
import time


class AppProxyError(Exception):
    pass


class AppProxyClient(threading.Thread):
    def __init__(self, sensor_queue, result_queue, ipaddress, port):
        self.stop = threading.Event()
        self.port_number = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((ipaddress, port))
        self.sensor_queue = sensor_queue
        self.result_queue = result_queue
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
            sys.stdout.write("Server is disconnected unexpectedly\n")
        self.sock.close()

    def terminate(self):
        self.stop.set()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            data += self.sock.recv(recv_size - len(data))
        return data

    def _handle_video_streaming(self):
        sys.stdout.write("receiving new image\n")
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        data_size = struct.unpack("!I", self._recv_all(4))[0]
        header = self._recv_all(header_size)
        json_header = json.loads(header)
        data = self._recv_all(data_size)
        self.sensor_queue.put(json_header, data)

    def _handle_result_output(self):
        pass
        '''
        while self.result_queue.empty() is False:
            return_data = self.result_queue.get()
            header_size = struct.pack("!I", len(json_header))
            self.request.send(header_size)
            self.wfile.write(json_header)
            self.wfile.flush()
        '''

if __name__ == "__main__":
    try:
        image_queue = Queue.Queue()
        output_queue = Queue.Queue()
        client = AppProxyClient(image_queue, output_queue, "128.2.210.197", 10101)
        client.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
        client.terminate()
    except Exception as e:
        client.terminate()

