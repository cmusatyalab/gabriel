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

class Protocol_client(object):
    CONTROL_MESSAGE_KEY = "control"
    RESULT_MESSAGE_KEY = "result"
    RESULT_ID_MESSAGE_KEY = "result_frame_id"
    FRAME_MESSAGE_KEY = "id"
    OFFLOADING_ENGINE_NAME_KEY = "offload_name"
    TOKEN_INJECT_KEY = "token_inject"


class Protocol_application(object):
    JSON_KEY_SENSOR_TYPE = "sensor_type"
    JSON_VALUE_SENSOR_TYPE_JPEG = "mjepg"
    JSON_VALUE_SENSOR_TYPE_ACC = "acc"
    JSON_VALUE_SENSOR_TYPE_GPS = "gps"
    JSON_VALUE_SENSOR_TYPE_AUDIO = "wave"

class Protocol_measurement(object):
    JSON_KEY_CONTROL_SENT_TIME = "control_sent_time"
    JSON_KEY_APP_RECV_TIME = "app_recv_time"
    JSON_KEY_APP_SENT_TIME = "app_sent_time"
    JSON_KEY_UCOMM_RECV_TIME = "ucomm_recv_time"
    JSON_KEY_UCOMM_SENT_TIME = "ucomm_sent_time"
