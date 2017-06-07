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


import json
from optparse import OptionParser
import os
import Queue
import sys
from flask import Flask
from flask import request
from flask.ext import restful
from flask.ext.restful import abort
from flask.ext.restful import reqparse
from flask.ext.restful import Resource

dir_file = os.path.dirname(os.path.realpath(__file__))

sys.path.insert(0, os.path.join(dir_file, "../../.."))
import gabriel


KEY_SERVICE_NAME    = "service_name"
KEY_SERVICE_CONTENT = "service_content"
KEY_RET             = "return"

custom_service_list = list()

def process_command_line(argv):
    VERSION = 'gabriel control server : %s' % gabriel.Const.VERSION
    DESCRIPTION = "Gabriel cognitive assistance"

    parser = OptionParser(usage = '%prog [option]', version = VERSION,
            description = DESCRIPTION)

    parser.add_option(
            '-n', '--net_interface', action = 'store', default = "eth0",
            help = "the network interface with which the cognitive engines communicate")
    settings, args = parser.parse_args(argv)

    return settings, args

global net_interface
settings, args = process_command_line(sys.argv[1:])
net_interface = settings.net_interface

class CustomService(object):
    def __init__(self, service_name, data):
        self.service_name = service_name
        self.content = data

    def update_data(self, json_str):
        self.content = json_str

    def get_service_name(self):
        return self.service_name

    def get_data(self):
        return self.content


class UpdateService(Resource):
    def _find_service(self, requested_service):
        global custom_service_list

        matching_service = None
        for service_item in custom_service_list:
            if service_item.service_name == requested_service:
                matching_service = service_item
                return matching_service
        return matching_service

    def get(self, service_name):
        requested_service = service_name
        if requested_service is None:
            msg = "Need service ID"
            abort(404, message = msg)

        matching_service = self._find_service(service_name)
        if matching_service is None:
            msg = "Not a valid service name: %s" % service_name
            abort(404, message = msg)
        ret_msg = {
                KEY_RET: "success",
                KEY_SERVICE_NAME: matching_service.get_service_name(),
                KEY_SERVICE_CONTENT: matching_service.get_data(),
                }
        return ret_msg, 200


    def post(self, service_name):
        global custom_service_list

        existing_service = self._find_service(service_name)
        if existing_service is not None:
            msg = "Service %s exists. Delete first." % service_name
            abort(404, message = msg)

        data = dict(json.loads(request.data))
        new_service = CustomService(service_name, data)
        custom_service_list.append(new_service)

        # send response
        ret_msg = {
                KEY_RET: "success",
                KEY_SERVICE_NAME: new_service.get_service_name(),
                KEY_SERVICE_CONTENT: new_service.get_data(),
                }
        return ret_msg, 201

    def delete(self, service_name):
        requested_service = service_name
        if requested_service == None:
            msg = "Need service ID"
            abort(404, message = msg)
        matching_service = self._find_service(service_name)
        if matching_service == None:
            msg = "Not a valid service name: %s" % service_name
            abort(404, message = msg)
        custom_service_list.remove(matching_service)
        ret_msg = {
                KEY_RET: "success",
                KEY_SERVICE_NAME: matching_service.get_service_name(),
                KEY_SERVICE_CONTENT: matching_service.get_data(),
                }
        return ret_msg, 202


class ManageService(Resource):
    def get(self):
        ret_list = list()
        for service in custom_service_list:
            ret_list.append({
                    KEY_SERVICE_NAME: service.get_service_name(),
                    KEY_SERVICE_CONTENT: service.get_data(),
                    })
        ret_msg = {
                KEY_RET: "success",
                "services": ret_list,
                }
        return ret_msg, 200


class GabrielInfo(Resource):
    parser = reqparse.RequestParser()
    parser.add_argument('task', type = str)
    global net_interface
    ip_addr = gabriel.network.get_ip(net_interface)
    service_info = {
            gabriel.ServiceMeta.UCOMM_SERVER_IP: None,
            gabriel.ServiceMeta.UCOMM_SERVER_PORT: None,
            gabriel.ServiceMeta.VIDEO_TCP_STREAMING_IP: str(ip_addr),
            gabriel.ServiceMeta.VIDEO_TCP_STREAMING_PORT: int(gabriel.Const.PUBLISH_SERVER_VIDEO_PORT),
            gabriel.ServiceMeta.ACC_TCP_STREAMING_IP: str(ip_addr),
            gabriel.ServiceMeta.ACC_TCP_STREAMING_PORT: int(gabriel.Const.PUBLISH_SERVER_ACC_PORT),
            gabriel.ServiceMeta.AUDIO_TCP_STREAMING_IP: str(ip_addr),
            gabriel.ServiceMeta.AUDIO_TCP_STREAMING_PORT: int(gabriel.Const.PUBLISH_SERVER_AUDIO_PORT),
            gabriel.ServiceMeta.UCOMM_RELAY_IP: str(ip_addr),
            gabriel.ServiceMeta.UCOMM_RELAY_PORT: int(gabriel.Const.UCOMM_COMMUNICATE_PORT),
            }

    def get(self):
        return self.service_info, 200

    def put(self):
        '''
        Registers new ucomm address
        '''
        data = dict(json.loads(request.data))
        ucomm_ip = data.get(gabriel.ServiceMeta.UCOMM_SERVER_IP, None)
        ucomm_port = data.get(gabriel.ServiceMeta.UCOMM_SERVER_PORT, None)
        # check if address exists
        if ucomm_ip is None or ucomm_port is None:
            msg = "no valid ucomm address provided"
            abort(404, message = msg)
        # check if ucomm has already been registered
        original_ucomm_ip = self.service_info.get(gabriel.ServiceMeta.UCOMM_SERVER_IP, None)
        original_ucomm_port = self.service_info.get(gabriel.ServiceMeta.UCOMM_SERVER_PORT, None)
        if original_ucomm_ip is not None:
            LOG.warning("registering ucomm server, but old address exists")

        # update
        self.service_info[gabriel.ServiceMeta.UCOMM_SERVER_IP] = ucomm_ip
        self.service_info[gabriel.ServiceMeta.UCOMM_SERVER_PORT] = ucomm_port

        # send response
        ret_msg = {
                KEY_RET: "success",
                "objects": self.service_info,
                }
        return ret_msg, 202


parser = reqparse.RequestParser()
## run REST server
app = Flask(__name__)
api = restful.Api(app)
api.add_resource(GabrielInfo, '/')
# the service registry is currently not used
#api.add_resource(ManageService, '/services/')
#api.add_resource(UpdateService, '/services/<string:service_name>')

# do no turn on debug mode. it make a mess for graceful terminate
#app.run(debug=True)
app.run(host="0.0.0.0", port = gabriel.Const.SERVICE_DISCOVERY_HTTP_PORT)
