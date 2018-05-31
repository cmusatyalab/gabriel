#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Zhuo Chen <zhuoc@cs.cmu.edu>
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

from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import json
import multiprocessing
import os
import pprint
import Queue
import re
from SocketServer import ThreadingMixIn
import sys
import threading
import time

import gabriel
import gabriel.control
LOG = gabriel.logging.getLogger(__name__)

dir_file = os.path.dirname(os.path.realpath(__file__))

class MJPEGStreamHandler(BaseHTTPRequestHandler, object):
    def do_POST(self):
        pass

    def do_GET(self):
        try:
            self.path = self.path.split('?')[0]
            print self.path

            if self.path.endswith(".mjpeg"):
                if self.path.endswith("camera.mjpeg"):
                    data_queue = gabriel.control.input_display_queue
                elif self.path.endswith("output.mjpeg"):
                    data_queue = gabriel.control.output_display_queue_dict['image']
                elif self.path.endswith("debug.mjpeg"):
                    data_queue = gabriel.control.output_display_queue_dict['debug']
                self.send_response(200)
                self.wfile.write("Content-Type: multipart/x-mixed-replace; boundary=--aaboundary")
                self.wfile.write("\r\n\r\n")
                while 1:
                    if self.server.stopped:
                        break

                    try:
                        image_data = data_queue.get_nowait()

                        self.wfile.write("--aaboundary\r\n")
                        self.wfile.write("Content-Type: image/jpeg\r\n")
                        self.wfile.write("Content-length: " + str(len(image_data)) + "\r\n\r\n")
                        self.wfile.write(image_data)
                        self.wfile.write("\r\n\r\n\r\n")
                        time.sleep(0.001)

                    except Queue.Empty as e:
                        pass

            elif self.path.endswith(".jpeg"):
                data_queue = gabriel.control.input_display_queue

                try:
                    image_data = data_queue.get_nowait()
                    self.send_response(200)
                    self.send_header('Content-type', 'image/jpeg')
                    self.end_headers()
                    self.wfile.write(image_data)

                except Queue.Empty as e:
                    pass

            elif self.path.endswith("speech"):
                data_queue = gabriel.control.output_display_queue_dict['text']
                try:
                    speech_data = data_queue.get_nowait()
                    self.send_response(200)
                    self.send_header('Content-type',	'text/html')
                    self.end_headers()
                    self.wfile.write(speech_data)

                except Queue.Empty as e:
                    self.send_response(200)
                    self.send_header('Content-type',	'text/html')
                    self.end_headers()
                    self.wfile.write("")

            elif self.path.endswith("video"):
                data_queue = gabriel.control.output_display_queue_dict['video']
                try:
                    video_url = data_queue.get_nowait()
                    self.send_response(200)
                    self.send_header('Content-type',	'text/html')
                    self.end_headers()
                    self.wfile.write(video_url)

                except Queue.Empty as e:
                    self.send_response(200)
                    self.send_header('Content-type',	'text/html')
                    self.end_headers()
                    self.wfile.write("")

            else:
                f = open(dir_file + os.sep + self.path)
                self.send_response(200)
                self.send_header('Content-type',	'text/html')
                self.end_headers()
                self.wfile.write(f.read())
                f.close()

            return
        except IOError:
            self.send_error(404,'File Not Found: %s' % self.path)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    stopped = False
    """Handle requests in a separate thread."""
    def serve_forever(self):
        while not self.stopped:
            self.handle_request()

    def terminate(self):
        self.server_close()
        self.stopped = True

        # close all thread
        if self.socket != -1:
            self.socket.close()


if __name__ == "__main__":

    # http server
    http_server = ThreadedHTTPServer(('0.0.0.0', 7070), MJPEGStreamHandler)
    http_server_thread = threading.Thread(target = http_server.serve_forever)
    http_server_thread.daemon = True
    http_server_thread.start()

    try:
        while True:
            time.sleep(1)
    except Exception as e:
        pass
    except KeyboardInterrupt as e:
        LOG.info("user exits\n")
    finally:
        if http_server is not None:
            http_server.terminate()
