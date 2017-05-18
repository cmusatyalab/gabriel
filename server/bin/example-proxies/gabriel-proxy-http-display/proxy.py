#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Kiryong Ha <krha@cmu.edu>
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
from optparse import OptionParser
import os
import pprint
import Queue
import re
from SocketServer import ThreadingMixIn
import sys
import threading
import time

dir_file = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(dir_file, "../../.."))
import gabriel
import gabriel.proxy
LOG = gabriel.logging.getLogger(__name__)

share_queue = Queue.Queue(gabriel.Const.MAX_FRAME_SIZE)


class HttpProxy(gabriel.proxy.CognitiveProcessThread):
    def handle(self, header, data):
        header['status'] = "success"
        global share_queue
        if share_queue.full():
            try:
                share_queue.get_nowait()
            except Queue.Empty as e:
                pass
        share_queue.put(data)
        return json.dumps({})


class MJPEGStreamHandler(BaseHTTPRequestHandler, object):
    def do_GET(self):
        global share_queue
        self.data_queue = share_queue
        self.result_queue = Queue.Queue()

        try:
            self.path = re.sub('[^.a-zA-Z0-9]', "", str(self.path))
            if self.path== "" or self.path is None or self.path[:1] == ".":
                return
            if self.path.endswith(".html"):
                f = open(os.curdir + os.sep + self.path)
                self.send_response(200)
                self.send_header('Content-type',	'text/html')
                self.end_headers()
                self.wfile.write(f.read())
                f.close()
            elif self.path.endswith(".mjpeg"):
                self.send_response(200)
                self.wfile.write("Content-Type: multipart/x-mixed-replace; boundary=--aaboundary")
                self.wfile.write("\r\n\r\n")
                while 1:
                    image_data = self.data_queue.get()
                    self.wfile.write("--aaboundary\r\n")
                    self.wfile.write("Content-Type: image/jpeg\r\n")
                    self.wfile.write("Content-length: " + str(len(image_data)) + "\r\n\r\n")
                    self.wfile.write(image_data)
                    self.wfile.write("\r\n\r\n\r\n")
                    time.sleep(0.001)
            elif self.path.endswith(".jpeg"):
                f = open(curdir + sep + self.path)
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
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
    settings = gabriel.util.process_command_line(sys.argv[1:])

    ip_addr, port = gabriel.network.get_registry_server_address(settings.address)
    service_list = gabriel.network.get_service_list(ip_addr, port)
    LOG.info("Gabriel Server :")
    LOG.info(pprint.pformat(service_list))

    video_ip = service_list.get(gabriel.ServiceMeta.VIDEO_TCP_STREAMING_IP)
    video_port = service_list.get(gabriel.ServiceMeta.VIDEO_TCP_STREAMING_PORT)
    ucomm_ip = service_list.get(gabriel.ServiceMeta.UCOMM_SERVER_IP)
    ucomm_port = service_list.get(gabriel.ServiceMeta.UCOMM_SERVER_PORT)

    # image receiving thread
    image_queue = Queue.Queue(gabriel.Const.APP_LEVEL_TOKEN_SIZE)
    print "TOKEN SIZE OF OFFLOADING ENGINE: %d" % gabriel.Const.APP_LEVEL_TOKEN_SIZE
    video_streaming = gabriel.proxy.SensorReceiveClient((video_ip, video_port), image_queue)
    video_streaming.start()
    video_streaming.isDaemon = True

    # app proxy
    result_queue = multiprocessing.Queue()

    app_proxy = HttpProxy(image_queue, result_queue, engine_id = "HTTP")
    app_proxy.start()
    app_proxy.isDaemon = True

    # http server
    http_server = ThreadedHTTPServer(('0.0.0.0', 7070), MJPEGStreamHandler)
    http_server_thread = threading.Thread(target = http_server.serve_forever)
    http_server_thread.daemon = True
    http_server_thread.start()

    # result pub/sub
    result_pub = gabriel.proxy.ResultPublishClient((ucomm_ip, ucomm_port), result_queue)
    result_pub.start()
    result_pub.isDaemon = True

    try:
        while True:
            time.sleep(1)
    except Exception as e:
        pass
    except KeyboardInterrupt as e:
        LOG.info("user exits\n")
    finally:
        if video_streaming is not None:
            video_streaming.terminate()
        if app_proxy is not None:
            app_proxy.terminate()
        print dir(http_server_thread)
        if http_server is not None:
            http_server.terminate()
        result_pub.terminate()
