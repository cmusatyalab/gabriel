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
from app_server import ApplicationServer
from app_server import VideoSensorHandler
from app_server import AccSensorHandler
from ucomm_server import UCommServer, UCommHandler
from BaseHTTPServer import BaseHTTPRequestHandler
from http_streaming_server import MJPEGStreamHandler
from http_streaming_server import ThreadedHTTPServer
import mobile_server

import log as logging
from config import Const
from RESTServer_binder import RESTServer, RESTServerError
from upnp_server import UPnPServer, UPnPError


LOG = logging.getLogger(__name__)
rest_server = RESTServer()
upnp_server = UPnPServer()


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


def start_discovery_and_rest():
    global rest_server
    global upnp_server
    # start REST server for meta info
    try:
        rest_server.start()
    except RESTServerError as e:
        LOG.warning(str(e))
        LOG.warning("Cannot start REST API Server")
        rest_server = None
    LOG.info("Start RESTful API Server")

    # Start UPnP Server
    try:
        upnp_server.start()
    except UPnPError as e:
        LOG.warning(str(e))
        LOG.warning("Cannot start UPnP Server")
        upnp_server = None
    LOG.info("Start UPnP Server")


def finish_discovery_and_rest():
    global rest_server
    global upnp_server

    if upnp_server is not None:
        LOG.info("[TERMINATE] Terminate UPnP Server")
        upnp_server.terminate()
        upnp_server.join()
    if rest_server is not None:
        LOG.info("[TERMINATE] Terminate REST API monitor")
        rest_server.terminate()
        rest_server.join()


def main():
    settings, args = process_command_line(sys.argv[1:])

    start_discovery_and_rest()

    m_video_server = None
    m_acc_server = None
    m_result_server = None
    ucomm_server = None
    a_video_server = None
    a_acc_server = None
    if settings.image_dir:
        m_video_server = EmulatedMobileDevice(os.path.abspath(settings.image_dir))
    else:
        m_video_server = MobileCommServer(Const.MOBILE_SERVER_VIDEO_PORT, MobileVideoHandler)
    m_acc_server = MobileCommServer(Const.MOBILE_SERVER_ACC_PORT, MobileAccHandler)
    m_result_server = MobileCommServer(Const.MOBILE_SERVER_RESULT_PORT, MobileResultHandler)
    a_video_server = ApplicationServer(Const.APP_SERVER_VIDEO_PORT, VideoSensorHandler)
    a_acc_server = ApplicationServer(Const.APP_SERVER_ACC_PORT, AccSensorHandler)
    ucomm_server = UCommServer(Const.UCOMM_COMMUNICATE_PORT, UCommHandler)
    http_server = ThreadedHTTPServer(('0.0.0.0', 8080), MJPEGStreamHandler)

    m_video_server_thread = threading.Thread(target=m_video_server.serve_forever)
    m_acc_server_thread = threading.Thread(target=m_acc_server.serve_forever)
    m_result_server_thread = threading.Thread(target=m_result_server.serve_forever)
    a_video_server_thread = threading.Thread(target=a_video_server.serve_forever)
    a_acc_server_thread = threading.Thread(target=a_acc_server.serve_forever)
    ucomm_thread = threading.Thread(target=ucomm_server.serve_forever)
    http_server_thread = threading.Thread(target=http_server.serve_forever)
    m_video_server_thread.daemon = True
    m_acc_server_thread.daemon = True
    m_result_server_thread.daemon = True
    a_video_server_thread.daemon = True
    a_acc_server_thread.daemon = True
    ucomm_thread.daemon = True
    http_server_thread.daemon = True

    all_thread_list = [m_video_server_thread, m_acc_server_thread, \
            m_result_server_thread, a_video_server_thread, a_acc_server_thread, \
            ucomm_thread, http_server_thread]

    exit_status = 1
    try:
        m_video_server_thread.start()
        m_acc_server_thread.start()
        m_result_server_thread.start()
        a_video_server_thread.start()
        a_acc_server_thread.start()
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
        if a_acc_server is not None:
            a_acc_server.terminate()
        finish_discovery_and_rest()

    '''
    for each_thread in all_thread_list:
        if each_thread.is_alive() == True:
            import pdb;pdb.set_trace()
    '''
    return exit_status

if __name__ == '__main__':
    ret = main()
    sys.exit(ret)
