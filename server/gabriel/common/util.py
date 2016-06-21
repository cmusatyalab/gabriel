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

import json
from optparse import OptionParser
import pprint

import gabriel
LOG = gabriel.logging.getLogger(__name__)

def print_rtn(rtn_json):
    '''
    print return message in a nicer way:
    replace random bytes in image data with summarized info
    '''
    return pprint.pformat(rtn_json)


def process_command_line(argv):
    VERSION = gabriel.Const.VERSION
    DESCRIPTION = "Gabriel Cognitive Assistant"

    parser = OptionParser(usage = '%prog [option]', version = VERSION, description = DESCRIPTION)
    parser.add_option(
            '-s', '--address', action = 'store',
            help = "(IP address:port number) of directory server")

    settings, args = parser.parse_args(argv)
    if len(args) >= 1:
        parser.error("invalid arguement")

    if hasattr(settings, 'address') and settings.address is not None:
        if settings.address.find(":") == -1:
            parser.error("Need address and port. Ex) 10.0.0.1:8081")
    return settings


def add_preceding_zeros(n, total_length = 5):
    if n < 10:
        return "0" * (total_length - 1) + str(n)
    elif n < 100:
        return "0" * (total_length - 2) + str(n)
    elif n < 1000:
        return "0" * (total_length - 3) + str(n)
    elif n < 10000:
        return "0" * (total_length - 4) + str(n)
    elif n < 100000:
        return "0" * (total_length - 5) + str(n)
    elif n < 1000000:
        return "0" * (total_length - 6) + str(n)

    return None
