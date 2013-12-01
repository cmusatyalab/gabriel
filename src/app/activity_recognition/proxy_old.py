#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Kiryong Ha <krha@cmu.edu>
#           Zhuo Chen <zhuoc@cs.cmu.edu>
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

class ClientToControl(threading.Thread):
    def __init__(self, sensor_queue, result_queue, ipaddress, port):
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect((ipaddress, port))
        self.sensor_queue = sensor_queue
        self.result_queue = result_queue
        threading.Thread.__init__(self, target=self.streaming)

    def streaming(self):
        sys.stdout.write("Connected to control VM at %s:%d\n" % self.sock.getpeername())
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
        #sys.stdout.write("receiving new image\n")
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        data_size = struct.unpack("!I", self._recv_all(4))[0]
        header = self._recv_all(header_size)
        json_header = json.loads(header)
        data = self._recv_all(data_size)
        self.sensor_queue.put((header, data))
        print "received a frame"

    def _handle_result_output(self):
        while not self.result_queue.empty():
            message = self.result_queue.get()
            data = json.dumps({"result" : message})
            print "message sent: %s" % data
            data_size = struct.pack("!I", len(data))
            #self.sock.sendall(data_size)
            #self.sock.sendall(data)


class ClientToApp(threading.Thread):
    def __init__(self, sensor_queue, result_queue, ipaddress, port):
        self.stop = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect((ipaddress, port))
        self.sensor_queue = sensor_queue
        self.result_queue = result_queue
        threading.Thread.__init__(self, target=self.streaming)

    def streaming(self):
        sys.stdout.write("Connected to application at %s:%d\n" % self.sock.getpeername())
        socket_fd = self.sock.fileno()
        input_list = [socket_fd]
        output_list = [socket_fd]
        try:
            while(not self.stop.wait(0.01)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, [])
                for s in inputready:
                    if s == socket_fd:
                        self._handle_result_input()
                for s in outputready:
                    if s == socket_fd:
                        self._handle_video_streaming()
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

    def _handle_result_input(self):
        pass

    def _handle_video_streaming(self):
        while not self.sensor_queue.empty():
            (header, data) = self.sensor_queue.get()
            data_size = struct.pack("!I", len(data))
            self.sock.sendall(data_size)
            self.sock.sendall(data)


if __name__ == "__main__":
    try:
        image_queue = Queue.Queue()
        output_queue = Queue.Queue()
        clientToControl = ClientToControl(image_queue, output_queue, "54.202.91.97", 10101)
        clientToControl.start()
        clientToApp = ClientToApp(image_queue, output_queue, "localhost", 8080)
        clientToApp.start()

        address = ('localhost', 18080)
        result_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        result_socket.bind(address)
        while True:
            received, addr = result_socket.recvfrom(2048)
            output_queue.put(received)
        #    time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits: %s\n" % str(e))
        clientToControl.terminate()
        clientToApp.terminate()
    except Exception as e:
        sys.stderr.write("error: %s" % str(e))
        clientToControl.terminate()
        clientToApp.terminate()

