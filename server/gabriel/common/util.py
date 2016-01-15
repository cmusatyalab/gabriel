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
import pprint

import gabriel
LOG = gabriel.logging.getLogger(__name__)

def print_rtn(rtn_json):
    '''
    print return message in a nicer way:
    replace random bytes in image data with summarized info
    '''
    result_str = rtn_json[gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE]
    result_json = json.loads(result_str)
    image_str = result_json.get(gabriel.Protocol_result.JSON_KEY_IMAGE, None)
    if image_str is not None:
        result_json[gabriel.Protocol_client.RESULT_MESSAGE_KEY] = "an image of %d bytes" % len(image_str)
    result_str = json.dumps(result_json)
    rtn_json[gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE] = result_str

    return pprint.pformat(rtn_json)

