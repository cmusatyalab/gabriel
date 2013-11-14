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
from mobile_server import MobileVideoHandler
from mobile_server import MobileAccHandler
from mobile_server import MobileResultHandler
from app_server import VideoSensorServer
from ucomm_server import UCommServer, UCommHandler
import mobile_server
from BaseHTTPServer import BaseHTTPRequestHandler

import log as logging
from config import Const


LOG = logging.getLogger(__name__)


class MJPEGStreamHandler(BaseHTTPRequestHandler, object):
    def do_GET(self):
        self.data_queue = Queue.Queue(Const.MAX_FRAME_SIZE)
        self.result_queue = Queue.Queue()
        mobile_server.image_queue_list.append(self.data_queue)

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
    stopped = False
    #class ThreadedHTTPServer(HTTPServer):
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


def main():
    settings, args = process_command_line(sys.argv[1:])

    m_video_server = None
    m_acc_server = None
    m_result_server = None
    ucomm_server = None
    a_video_server = None
    if settings.image_dir:
        m_video_server = EmulatedMobileDevice(os.path.abspath(settings.image_dir))
    else:
        m_video_server = MobileCommServer(Const.MOBILE_SERVER_VIDEO_PORT, MobileVideoHandler)
    m_acc_server = MobileCommServer(Const.MOBILE_SERVER_ACC_PORT, MobileAccHandler)
    m_result_server = MobileCommServer(Const.MOBILE_SERVER_RESULT_PORT, MobileResultHandler)
    a_video_server = VideoSensorServer(sys.argv[1:])
    ucomm_server = UCommServer(Const.UCOMM_COMMUNICATE_PORT, UCommHandler)
    http_server = ThreadedHTTPServer(('localhost', 8080), MJPEGStreamHandler)

    m_video_server_thread = threading.Thread(target=m_video_server.serve_forever)
    m_acc_server_thread = threading.Thread(target=m_acc_server.serve_forever)
    m_result_server_thread = threading.Thread(target=m_result_server.serve_forever)
    a_video_server_thread = threading.Thread(target=a_video_server.serve_forever)
    ucomm_thread = threading.Thread(target=ucomm_server.serve_forever)
    http_server_thread = threading.Thread(target=http_server.serve_forever)
    m_video_server_thread.daemon = True
    m_acc_server_thread.daemon = True
    m_result_server_thread.daemon = True
    a_video_server_thread.daemon = True
    ucomm_thread.daemon = True
    http_server_thread.daemon = True

    exit_status = 1
    try:
        m_video_server_thread.start()
        m_acc_server_thread.start()
        m_result_server_thread.start()
        a_video_server_thread.start()
        ucomm_thread.start()
        http_server_thread.start()

        while True:
            time.sleep(100)
    except Exception as e:
        sys.stderr.write(str(e))
        exit_status = 1
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        exit_status = 0
    finally:
        if m_video_server is not None:
            m_video_server.terminate()
        if m_acc_server is not None:
            m_acc_server.terminate()
        if m_result_server is not None:
            m_result_server.terminate()
        if ucomm_server is not None:
            ucomm_server.terminate()
        if a_video_server is not None:
            a_video_server.terminate()

    return exit_status

if __name__ == '__main__':
    ret = main()
    sys.exit(ret)
