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
import os
from optparse import OptionParser

from os import curdir, sep
import threading
import Queue
import re
import json
from mobile_server import MobileCommServer
from app_server import VideoSensorServer
import mobile_server
from BaseHTTPServer import BaseHTTPRequestHandler

import log as logging
from protocol import Protocol_client
from config import Const


LOG = logging.getLogger(__name__)


class MJPEGStreamHandler(BaseHTTPRequestHandler, object):
    def do_GET(self):
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        self.result_queue = Queue.Queue()
        mobile_server.image_queue_list.append(self.data_queue)
        mobile_server.result_queue_list.append(self.result_queue)

        try:
            self.path = re.sub('[^.a-zA-Z0-9]', "", str(self.path))
            if self.path== "" or self.path is None or self.path[:1] == ".":
                return
            if self.path.endswith(".html"):
                f = open(curdir + sep + self.path)
                self.send_response(200)
                self.send_header('Content-type',	'text/html')
                self.end_headers()
                self.wfile.write(f.read())
                f.close()
                return
            if self.path.endswith(".mjpeg"):
                self.send_response(200)
                self.wfile.write("Content-Type: multipart/x-mixed-replace; boundary=--aaboundary")
                self.wfile.write("\r\n\r\n")
                while 1:
                    if self.data_queue.empty() == False:
                        image_data = self.data_queue.get()
                        #LOG.info("getting new image")
                        self.wfile.write("--aaboundary\r\n")
                        self.wfile.write("Content-Type: image/jpeg\r\n")
                        self.wfile.write("Content-length: " + str(len(image_data)) + "\r\n\r\n")
                        self.wfile.write(image_data)
                        self.wfile.write("\r\n\r\n\r\n")
                        time.sleep(0.01)
                return
            if self.path.endswith(".jpeg"):
                f = open(curdir + sep + self.path)
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
                self.end_headers()
                self.wfile.write(f.read())
                f.close()
                return
            return
        except IOError:
            self.send_error(404,'File Not Found: %s' % self.path)


def process_command_line(argv):
    VERSION = 'gabriel server : %s' % Const.VERSION
    DESCRIPTION = "Gabriel cognitive assistance"

    parser = OptionParser(usage='%prog [option]', version=VERSION, 
            description=DESCRIPTION)

    parser.add_option(
            '-e', '--emulation', action='store', dest='image_dir',
            help="emulate mobile device using series of jpeg images")
    settings, args = parser.parse_args(argv)
    if len(args) >= 1:
        parser.error("invalid arguement")

    if hasattr(settings, 'image_dir') and settings.image_dir is not None:
        if os.path.isdir(settings.image_dir) is False:
            parser.error("%s is not a directory" % settings.image_dir)
    return settings, args


class EmulatedMobileDevice(object):
    def __init__(self, image_dir):
        from os import listdir
        self.stop = threading.Event()
        self.filelist = [os.path.join(image_dir, f) for f in listdir(image_dir)
                if f.lower().endswith("jpeg") or f.lower().endswith("jpg")]
        self.filelist.sort()

    def serve_forever(self):
        frame_count = 0;
        while(not self.stop.wait(0.01)):
            for image_file in self.filelist:
                image_data = open(image_file, "r").read()
                for image_queue in mobile_server.image_queue_list:
                    header_data = json.dumps({"type":"emulated", "id":frame_count})
                    if image_queue.full() is True:
                        image_queue.get()
                    image_queue.put((header_data, image_data))
                if frame_count%100 == 0:
                    pass
                    #LOG.info("pushing emualted image to the queue (%d)" % frame_count)
                frame_count += 1
                time.sleep(0.02)

    def terminate(self):
        self.stop.set()
        pass


from SocketServer import ThreadingMixIn
from BaseHTTPServer import HTTPServer
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    #class ThreadedHTTPServer(HTTPServer):
    """Handle requests in a separate thread."""


def main():
    settings, args = process_command_line(sys.argv[1:])

    if settings.image_dir:
        # use emulated images for the testing
        m_server = EmulatedMobileDevice(os.path.abspath(settings.image_dir))
    else:
        m_server = MobileCommServer(sys.argv[1:])

    video_server = VideoSensorServer(sys.argv[1:])
    http_server = ThreadedHTTPServer(('localhost', 8080), MJPEGStreamHandler)

    m_server_thread = threading.Thread(target=m_server.serve_forever)
    m_server_thread.daemon = True
    video_server_thread = threading.Thread(target=video_server.serve_forever)
    video_server_thread.daemon = True
    http_server_thread = threading.Thread(target=http_server.serve_forever)
    http_server_thread.daemon = True

    try:
        m_server_thread.start()
        video_server_thread.start()
        http_server_thread.start()

        emulated_result_queue = Queue.Queue()
        mobile_server.result_queue_list.append(emulated_result_queue)
        while True:
            time.sleep(100)
            #user_input = raw_input("Enter q to quit: ")
            #if user_input.lower() == 'q':
            #    break
            #else:
            #    json_result = json.dumps({
            #        Protocol_client.RESULT_MESSAGE_KEY: str(user_input),
            #        })
            #    emulated_result_queue.put(json_result)
    except Exception as e:
        sys.stderr.write(str(e))
        m_server.terminate()
        sys.exit(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        m_server.terminate()
        sys.exit(1)
    else:
        m_server.terminate()
        sys.exit(0)

if __name__ == '__main__':
    main()
