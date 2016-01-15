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

import httplib
import json
import urllib2
import urlparse


class HttpConnectionError(Exception):
    pass


def http_get(url, rtn_format = "json"):
    conn = urllib2.urlopen("%s" % (url))
    rtn_data = conn.read()
    if rtn_format == "json":
        return json.loads(rtn_data)
    elif rtn_format == "raw":
        return rtn_data


def http_post(url, json_string, rtn_format = "json"):
    end_point = urlparse.urlparse("%s" % url)
    params = json.dumps(json_string)
    headers = {"Content-type": "application/json"}

    conn = httplib.HTTPConnection(end_point[1])
    conn.request("POST", "%s" % end_point[2], params, headers)
    response = conn.getresponse()
    rtn_data = response.read()
    conn.close()
    if rtn_format == "json":
        return json.loads(rtn_data)
    elif rtn_format == "raw":
        return rtn_data


def http_put(url, json_string, rtn_format = "json"):
    end_point = urlparse.urlparse("%s" % url)
    params = json.dumps(json_string)
    headers = {"Content-type": "application/json"}

    conn = httplib.HTTPConnection(end_point[1])
    conn.request("PUT", "%s" % end_point[2], params, headers)
    response = conn.getresponse()
    rtn_data = response.read()
    conn.close()
    if rtn_format == "json":
        return json.loads(rtn_data)
    elif rtn_format == "raw":
        return rtn_data
