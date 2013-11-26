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
import sys
sys.path.insert(0, "../common")
import time
import Queue

import socket
import struct
import sys
import Queue
import time

from launcher import AppLauncher
from app_proxy import AppProxyError
from app_proxy import AppProxyStreamingClient
from app_proxy import AppProxyThread
from app_proxy import ResultpublishClient
from app_proxy import get_service_list
from app_proxy import SERVICE_META
from app_proxy import LOG


class MOPEDThread(AppProxyThread):
    def __init__(self, app_addr, image_queue, output_queue_list):
        super(MOPEDThread, self).__init__(image_queue, output_queue_list)
        self.app_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.app_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.app_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.app_sock.connect(app_addr)

    def terminate(self):
        if self.app_sock is not None:
            self.app_sock.close()
        super(AppProxyThread, self).terminate()

    @staticmethod
    def _recv_all(socket, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = socket.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def handle(self, header, data):
        # receive data from control VM
        LOG.info("receiving new image")
        # feed data to the app
        packet = struct.pack("!I%ds" % len(data), len(data), data)
        self.app_sock.sendall(packet)
        result_size = struct.unpack("!I", self._recv_all(self.app_sock, 4))[0]
        result_data = self._recv_all(self.app_sock, result_size)
        sys.stdout.write("result : %s\n" % result_data)

        if len(result_data.strip()) != 0:
            return result_data
        return None


if __name__ == "__main__":
    APP_PATH = "./moped_server"
    APP_PORT = 9092

    app_thread = None
    proxy_thread = None
    client = None
    result_pub = None
    try:
        app_thread = AppLauncher(APP_PATH, is_print=False)
        app_thread.start()
        app_thread.isDaemon = True
        time.sleep(3)

        image_queue = Queue.Queue(1)
        output_queue_list = list()
        service_list = get_service_list()
        video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
        video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
        return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

        client = AppProxyStreamingClient((video_ip, video_port), image_queue)
        client.start()
        client.isDaemon = True
        proxy_thread = MOPEDThread(("127.0.0.1", APP_PORT), image_queue, output_queue_list)
        proxy_thread.start()
        proxy_thread.isDaemon = True
        result_pub = ResultpublishClient(return_addresses, output_queue_list)
        result_pub.start()
        result_pub.isDaemon = True

        LOG.info("Start receiving data\n")
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
    except Exception as e:
        pass
    finally:
        if client is not None:
            client.terminate()
        if app_thread is not None:
            app_thread.terminate()
        if result_pub is not None:
            result_pub.terminate()
        if proxy_thread is not None:
            proxy_thread.terminate()

