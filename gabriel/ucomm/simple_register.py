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

from urlparse import urlparse
import httplib
import json


def get(url):
    import urllib2
    meta_stream = urllib2.urlopen("%s" % (url))
    meta_raw = meta_stream.read()
    ret = json.loads(meta_raw)
    return ret

def post(url, json_string):
    end_point = urlparse("%s" % url)
    params = json.dumps(json_string)
    headers = {"Content-type": "application/json"}

    conn = httplib.HTTPConnection(end_point[1])
    conn.request("POST", "%s" % end_point[2], params, headers)
    response = conn.getresponse()
    data = response.read()
    dd = json.loads(data)
    if dd.get("return", None) != "success":
        msg = "Failed\n%s", str(dd)
        raise Exception(msg)
    conn.close()
    return dd


directory_url = "http://localhost:8021/services/test"
json_info = {
        "master_address": "1111:1111",
    }
ret = post(directory_url, json_info)
print ret
ret = get(directory_url)
print ret


