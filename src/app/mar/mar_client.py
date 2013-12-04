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
import sys
import socket
from optparse import OptionParser
import subprocess
import json
import tempfile
import time
import struct
import math

def get_local_ipaddress():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("gmail.com",80))
    ipaddress = (s.getsockname()[0])
    s.close()
    return ipaddress


def process_command_line(argv):
    global operation_mode

    parser = OptionParser(usage="usage: %prog [option]", version="MOPED Desktop Client")
    parser.add_option(
            '-i', '--input', action='store', type='string', dest='input_dir',
            help='Set Input image directory')
    parser.add_option(
            '-s', '--server', action='store', type='string', dest='server_address', default="localhost",
            help='Set Input image directory')
    parser.add_option(
            '-p', '--port', action='store', type='int', dest='server_port', default=9094,
            help='Set Input image directory')
    settings, args = parser.parse_args(argv)
    if not len(args) == 0:
        parser.error('program takes no command-line arguments; "%s" ignored.' % (args,))
    
    if not settings.input_dir:
        parser.error("input directory does no exists at :%s" % (settings.input_dir))
    if not os.path.isdir(settings.input_dir):
        parser.error("input directory does no exists at :%s" % (settings.input_dir))

    return settings, args


def send_request(sock, image):
    packet = struct.pack("!I%ds" % len(image),
            len(image),
            image)
    sent_size = sock.send(packet)
    if not sent_size == (4+len(image)):
        sys.stderr.write("Error, send wrong size of file : %d" % sent_size)
        sys.exit(1)
    
    #recv
    data = sock.recv(4)
    ret_size = struct.unpack("!I", data)[0]

    ret_data = None
    if not ret_size == 0:
        ret_data = sock.recv(ret_size)
    return ret_data


def batch_request(address, port, inputs):
    # connection
    try:
        print "Connecting to (%s, %d).." % (address, port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        #sock.setblocking(True)
        #sock.settimeout(120.0)
        sock.connect((address, port))
    except socket.error, msg:
        sys.stderr.write("Error, %s\n" % msg[1])
        sys.exit(1)

    # send requests
    current_duration = -1
    print "image\tstart\tend\tduration\tjitter"
    for each_input in inputs:
        start_time_request = time.time() * 1000.0
        binary = open(each_input, 'rb').read();
        length = os.path.getsize(each_input)
        if len(binary) != length:
            sys.stderr.write("Error, input length is wrong");
            sys.exit(1)

        # send
        ret_data = send_request(sock, binary)
        # print result
        end_time_request = time.time() * 1000.0
        prev_duration = current_duration
        current_duration = end_time_request-start_time_request

        if prev_duration == -1: # fisrt response
            print "%s\t%014.2f\t%014.2f\t%014.2f\t0" % (each_input, start_time_request,\
                    end_time_request, \
                    end_time_request-start_time_request)
        else:
            print "%s\t%014.2f\t%014.2f\t%014.2f\t%014.2f" % (each_input, round(start_time_request, 3), \
                    end_time_request, \
                    current_duration, \
                    math.fabs(current_duration-prev_duration))

def main(argv=None):
    global LOCAL_IPADDRESS
    settings, args = process_command_line(sys.argv[1:])

    files = [os.path.join(settings.input_dir, file) for file in os.listdir(settings.input_dir) if file[-4:].lower() == "jpeg"]
    batch_request(settings.server_address, settings.server_port, files)

    return 0


if __name__ == "__main__":
    status = main()
    sys.exit(status)
