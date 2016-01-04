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

import fcntl
import json
import socket
import struct
import urllib2

import gabriel


def get_ip(iface = 'eth0'):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockfd = sock.fileno()
    SIOCGIFADDR = 0x8915

    ifreq = struct.pack('16sH14s', iface, socket.AF_INET, '\x00' * 14)
    try:
        res = fcntl.ioctl(sockfd, SIOCGIFADDR, ifreq)
    except:
        return None
    ip = struct.unpack('16sH2x4s8x', res)[2]
    return socket.inet_ntoa(ip)


def get_service_list(address = None):
    UPnP_client = gabriel.network.UPnPClient()
    service_list = None
    if settings.address is None:
        UPnP_client.start()
        UPnP_client.join()
        service_list = UPnP_client.service_list
    else:
        ip_addr, port = address.split(":", 1)
        port = int(port)
        meta_stream = urllib2.urlopen("http://%s:%d/" % (ip_addr, port))
        meta_raw = meta_stream.read()
        service_list = json.loads(meta_raw)
    return service_list
