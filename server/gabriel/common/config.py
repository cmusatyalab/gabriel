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

import logging
import os


class ConfigurationError(Exception):
    pass


def which(program):
    '''
    Find the system program with name @program.
    Similar to the "which" command in bash.
    Returns the runnable app with correct path.
    Returns None is program doesn't exist.
    '''
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)
    fpath, fname = os.path.split(program)
    exe_file = None
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            path = path.strip('"')
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file
    return exe_file


class Const(object):
    VERSION = "0.3"

    LOG_FILE_PATH = "/var/tmp/cloudlet/log-gabriel"
    LOG_IMAGES_PATH = "/var/log/gabriel-images"
    LOG_VIDEO_PATH = "/var/log/gabriel-video.avi"

    ## port number for the server modules
    # communication with mobile
    MOBILE_SERVER_VIDEO_PORT = 9098
    MOBILE_SERVER_ACC_PORT = 9099
    MOBILE_SERVER_GPS_PORT = 9100
    MOBILE_SERVER_RESULT_PORT = 9101

    MOBILE_SERVER_CONTROL_PORT = 22222

    # servers that publish streams
    PUBLISH_SERVER_VIDEO_PORT = 10101
    PUBLISH_SERVER_ACC_PORT = 10102
    PUBLISH_SERVER_GPS_PORT = 10103

    # service discovery http server
    SERVICE_DISCOVERY_HTTP_PORT = 8021

    # communication between control and ucomm
    UCOMM_COMMUNICATE_PORT = 9090

    # communication between ucomm and engines
    UCOMM_SERVER_PORT = 10120

    MODULE_DIR = os.path.dirname(os.path.abspath(__file__))

    ## UPnP server & client
    UPnP_SERVER_PATH = os.path.abspath(os.path.join(MODULE_DIR, "../lib/gabriel_upnp_server.jar"))
    UPnP_SERVER_PATH = UPnP_SERVER_PATH if os.path.exists(UPnP_SERVER_PATH) else which("gabriel_upnp_server.jar")
    UPnP_CLIENT_PATH = os.path.abspath(os.path.join(MODULE_DIR, "../lib/gabriel_upnp_client.jar"))
    UPnP_CLIENT_PATH = UPnP_CLIENT_PATH if os.path.exists(UPnP_CLIENT_PATH) else which("gabriel_upnp_client.jar")

    # buffer size for application level flow control
    MAX_TOKEN_SIZE = 1 # buffer size between control engine and client
    MAX_FRAME_SIZE = MAX_TOKEN_SIZE # for compatibility reasons
    APP_LEVEL_TOKEN_SIZE = 1 # buffer size between control engine and cognitive engine
    TOKEN_INJECTION_SIZE = 10

    ## min allowed time between two identical messages
    DUPLICATE_MIN_INTERVAL = 3

    # backward-compatibility flag
    # if set to true (legacy way), the result will follow previous convention that it is a single json string with result as a value
    # if set to false (default), the result will send header as json, followed by arbitrary data type
    LEGACY_JSON_ONLY_RESULT = False



class ServiceMeta(object):
    UCOMM_SERVER_IP = "ucomm_server_ip"
    UCOMM_SERVER_PORT = "ucomm_server_port"
    VIDEO_TCP_STREAMING_IP = "video_tcp_streaming_ip"
    VIDEO_TCP_STREAMING_PORT = "video_tcp_streaming_port"
    ACC_TCP_STREAMING_IP = "acc_tcp_streaming_ip"
    ACC_TCP_STREAMING_PORT = "acc_tcp_streaming_port"
    UCOMM_RELAY_IP = "ucomm_relay_ip"
    UCOMM_RELAY_PORT = "ucomm_relay_port"


class Debug(object):
    TIME_MEASUREMENT = True
    LOG_LEVEL_FILE = logging.DEBUG
    LOG_LEVEL_CONSOLE = logging.INFO
    LOG_STAT = True
    DIRECT_RETURN = False
    SAVE_IMAGES = False
    SAVE_VIDEO = False
