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
    VERSION = "0.2"

    LOG_FILE_PATH = "/var/tmp/cloudlet/log-gabriel"

    ## port number for the server modules
    # communication with mobile
    MOBILE_SERVER_VIDEO_PORT = 9098
    MOBILE_SERVER_ACC_PORT = 9099
    MOBILE_SERVER_GPS_PORT = 9100
    MOBILE_SERVER_RESULT_PORT = 9101

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

    # TODO: what's this?
    OFFLOADING_MONITOR_PORT = 9091

    MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
    ## REST server
    # The REST server is currently implemented using flask.
    # No 3rd party implementation is used.
    #REST_SERVER_BIN = os.path.abspath(os.path.join(MODULE_DIR, "../lib/gabriel_REST_server"))
    #REST_SERVER_BIN = REST_SERVER_BIN if os.path.exists(REST_SERVER_BIN) else which("gabriel_REST_server")

    ## UPnP server & client
    UPnP_SERVER_PATH = os.path.abspath(os.path.join(MODULE_DIR, "../lib/gabriel_upnp_server.jar"))
    UPnP_SERVER_PATH = UPnP_SERVER_PATH if os.path.exists(UPnP_SERVER_PATH) else which("gabriel_upnp_server.jar")
    UPnP_CLIENT_PATH = os.path.abspath(os.path.join(MODULE_DIR, "../lib/gabriel_upnp_client.jar"))
    UPnP_CLIENT_PATH = UPnP_CLIENT_PATH if os.path.exists(UPnP_CLIENT_PATH) else which("gabriel_upnp_client.jar")

    ## TODO
    MAX_FRAME_SIZE = 1
    APP_LEVEL_TOKEN_SIZE = 1
    TOKEN_INJECTION_SIZE = 10

    ## min allowed time between two identical messages
    DUPLICATE_MIN_INTERVAL = 3


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
