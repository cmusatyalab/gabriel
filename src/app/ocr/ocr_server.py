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

from tesserwrap import Tesseract
from PIL import Image
import StringIO
import SocketServer
import sys
import select
import socket
import threading
import traceback
import time

tr = Tesseract(lang="eng")
tr.set_page_seg_mode(3)

import struct


class OCRServerHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(OCRServerHandler, self).setup()
        self.frame_count = 1
        self.output_file = open("output.log", "w")

    def terminate(self):
        if self.output_file != None:
            self.output_file.close()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise Exception("Socket is closed")
            data += tmp_data
        return data

    def _handle_image(self):
        img_size = struct.unpack("!I", self._recv_all(4))[0]
        image_data = self._recv_all(img_size)

        start_time = time.time()
        result_msg = run_ocr(image_data, force_return=True)
        end_time = time.time()
        print "result length : %d, compute time : %f" % \
                (len(result_msg), end_time-start_time)
        if result_msg is None or len(result_msg) == 0:
            result_msg = ""
            packet = struct.pack("!I", 0)
        else:
            packet = struct.pack("!I%ds" % len(result_msg),
                    len(result_msg),
                    result_msg)
        self.request.send(packet)
        self.wfile.flush()

        # logging
        self.output_file.write("frame: %d\n" % (self.frame_count))
        self.output_file.write(result_msg)
        self.output_file.write("\n")
        self.output_file.flush()
        #open("image-%d.jpg" % self.frame_count, "wb").write(image_data)
        self.frame_count += 1


    def handle(self):
        try:
            socket_fd = self.request.fileno()
            input_list = [socket_fd]
            output_list = [socket_fd]
            while True:
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, [])
                for s in inputready:
                    if s == socket_fd:
                        self._handle_image()
        except Exception as e:
            sys.stdout.write(traceback.format_exc())
            sys.stdout.write("%s" % str(e))
            sys.stdout.write("handler raises exception\n")
            sys.stdout.write("Client is disconnected")
            self.terminate()

    def terminate(self):
        pass


class OCRServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    def __init__(self, args):
        server_address = ('0.0.0.0', 10110)
        self.allow_reuse_address = True
        try:
            SocketServer.TCPServer.__init__(self, server_address, OCRServerHandler)
        except socket.error as e:
            sys.stderr.write(str(e))
            sys.stderr.write("Check IP/Port : %s\n" % (str(server_address)))
            sys.exit(1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("* Video Sensor server configuration")
        print(" - Open TCP Server at %s" % (str(server_address)))
        print(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(
                    socket.IPPROTO_TCP,
                    socket.TCP_NODELAY)))
        print("-" * 50)

    def handle_error(self, request, client_address):
        print("error")
        pass

    def terminate(self):
        if self.socket != -1:
            self.socket.close()

def run_ocr(image_data, force_return=False):
    buff = StringIO.StringIO()
    buff.write(image_data)
    buff.seek(0)
    image = Image.open(buff)
    tr.set_image(image)
    utf8str = tr.get_utf8_text()
    return_str = utf8str.encode("ascii", "ignore")
    #return_str = utf8str

    if force_return:
        return return_str
    else:
        if return_str.isalpha():
            return return_str
        else:
            print "OCR result is not letter"


if __name__ == "__main__":
    #data = open('test.jpg', 'rb').read()
    #print run_ocr(data, force_return=True)
    server = OCRServer(sys.argv[1:])
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    try:
        server_thread.start()
        while True:
            time.sleep(100)
    except Exception as e:
        sys.stderr.write(str(e))
        server.terminate()
        sys.exit(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        server.terminate()
        sys.exit(1)
    else:
        server.terminate()
        sys.exit(0)
