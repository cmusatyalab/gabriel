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

import os


class ConfigurationError(Exception):
    pass


def which(program):
    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK) 
    fpath, fname = os.path.split(program)
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
    VERSION = "0.1.1"

    # port number for the server modules
    MOBILE_SERVER_PORT = 9098
    SERVICE_DISCOVERY_HTTP_PORT = 8021
    VIDEO_PORT = 10101

    MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
    #which("gabriel_upnp_server.jar")
    UPnP_SERVER_PATH = os.path.abspath(os.path.join(MODULE_DIR, "./lib/gabriel_upnp_server.jar"))
    UPnP_CLIENT_PATH = os.path.abspath(os.path.join(MODULE_DIR, "./lib/gabriel_upnp_client.jar")) 
    REST_SERVER_BIN = os.path.abspath(os.path.join(MODULE_DIR, "./gabriel_REST_server"))
    LOG_FILE_PATH = "/var/tmp/cloudlet/log-gabriel"

    MAX_FRAME_SIZE = 3


class ServiceMeta(object):
    VIDEO_TCP_STREAMING_ADDRESS = "video_tcp_streaming_address"
    VIDEO_TCP_STREAMING_PORT = "video_tcp_streaming_port"
