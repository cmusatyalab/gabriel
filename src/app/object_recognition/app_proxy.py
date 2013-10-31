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
from launcher import AppLauncher


class AppProxyError(Exception):
    pass

class AppProxyClient(threading.Thread):
    def __init__(self, control_addr, app_addr):
        self.stop = threading.Event()
        self.port_number = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect(control_addr)
        self.app_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.app_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.app_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.app_sock.connect(app_addr)
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
            print str(e)
            sys.stdout.write("Server is disconnected unexpectedly\n")
        self.sock.close()
        self.app_sock.close()

    def terminate(self):
        self.stop.set()
        if self.sock is not None:
            self.sock.close()
        if self.app_sock is not None:
            self.app_sock.close()

    def _recv_all(self, sock, recv_size):
        data = ''
        while len(data) < recv_size:
            data += sock.recv(recv_size - len(data))
        return data

    def _handle_video_streaming(self):
        # receive data from control VM
        #sys.stdout.write("receiving new image\n")
        header_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        data_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        header = self._recv_all(self.sock, header_size)
        data = self._recv_all(self.sock, data_size)

        # feed data to the app
        packet = struct.pack("!I%ds" % len(data), len(data), data)
        self.app_sock.sendall(packet)
        result_size = struct.unpack("!I", self._recv_all(self.app_sock, 4))[0]
        result_data = self._recv_all(self.app_sock, result_size)
        #sys.stdout.write("result : %s\n" % result_data)
        if len(result_data.strip()) != 0:
            self.result_queue.put(
                    json.dumps({"result": "%s" % result_data})
                    )

            # wait. Otherwise repeating return
            time.sleep(1)

    def _handle_result_output(self):
        while self.result_queue.empty() is False:
            return_data = self.result_queue.get()
            packet = struct.pack("!I%ds" % len(return_data),
                    len(return_data), return_data)
            self.sock.sendall(packet)
            sys.stdout.write("returning result: %s\n" % return_data)

if __name__ == "__main__":
    APP_PATH = "./moped_server"
    APP_PORT = 9092

    app_thread = AppLauncher(APP_PATH, is_print=False)
    app_thread.start()
    app_thread.isDaemon = True
    time.sleep(3)

    sys.stdout.write("Start receiving data")
    control_addr = ("128.2.210.197", 10101)
    app_addr = ("127.0.0.1", APP_PORT)
    client = AppProxyClient(control_addr, app_addr)
    client.start()
    client.isDaemon = True
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
        client.terminate()
        app_thread.terminate()
    except Exception as e:
        client.terminate()
        app_thread.terminate()

