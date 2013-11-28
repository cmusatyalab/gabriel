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
sys.path.insert(0, "../../")
from control.protocol import Protocol_client
from control.protocol import Protocol_measurement
from control import log as logging
from control.upnp_client import UPnPClient
from control.config import Const
from control.config import ServiceMeta
from control.config import DEBUG

from optparse import OptionParser
import os
import multiprocessing
import tempfile
import traceback
import threading
import socket
import select
import struct
import json
import Queue
import time
import pprint


LOG = logging.getLogger(__name__)
upnp_client = UPnPClient()
CONST = Const
SERVICE_META = ServiceMeta


def get_service_list(argv=None):
    settings, args = process_command_line(sys.argv[1:])
    service_list = None
    if settings.address is None:
        upnp_client.start()
        upnp_client.join()
        service_list = upnp_client.service_list
    else:
        import urllib2
        ip_addr, port = settings.address.split(":", 1)
        port = int(port)
        meta_stream = urllib2.urlopen("http://%s:%d/" % (ip_addr, port))
        meta_raw = meta_stream.read()
        service_list = json.loads(meta_raw)
    pstr = pprint.pformat(service_list)
    LOG.info("Gabriel Server :")
    LOG.info(pstr)
    return service_list


def process_command_line(argv):
    VERSION = 'gabriel discovery'
    DESCRIPTION = "Gabriel service discovery"

    parser = OptionParser(usage='%prog [option]', version=VERSION,
            description=DESCRIPTION)

    parser.add_option(
            '-s', '--address', action='store', dest='address',
            help="(IP address:port number) of directory server")
    settings, args = parser.parse_args(argv)
    if len(args) >= 1:
        parser.error("invalid arguement")

    if hasattr(settings, 'address') and settings.address is not None:
        if settings.address.find(":") == -1:
            parser.error("Need address and port. Ex) 10.0.0.1:8081")
    return settings, args


class AppProxyError(Exception):
    pass

class AppProxyStreamingClient(threading.Thread):
    """
    This client will receive data from the control server as much as possible.
    And put the data into the queue, so that other thread can use the image
    """

    def __init__(self, control_addr, data_queue):
        self.stop = threading.Event()
        self.port_number = None
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.connect(control_addr)
            LOG.info("Success to connect to %s" % str(control_addr))
        except socket.error as e:
            raise AppProxyError("Failed to connect to %s" % str(control_addr))
        self.data_queue = data_queue
        self.stop_queue = multiprocessing.Queue()
        threading.Thread.__init__(self, target=self.streaming)

    def streaming(self):
        LOG.info("Start getting data from the server")
        try:
            stopfd = self.stop_queue._reader.fileno()
            socket_fd = self.sock.fileno()
            input_list = [socket_fd, stopfd]
            except_list = [socket_fd, stopfd]
            is_running = True
            while is_running:
                inputready, outputready, exceptready = \
                        select.select(input_list, [], except_list)
                for s in inputready:
                    if s == socket_fd:
                        self._handle_input_streaming()
                    if s == stopfd:
                        is_running = False
                for e in exceptready:
                    is_running = False
        except Exception as e:
            LOG.warning(traceback.format_exc())
            LOG.warning("%s" % str(e))
            LOG.warning("handler raises exception")
            LOG.warning("Server is disconnected unexpectedly")
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        if self.stop_queue is not None:
            self.stop_queue.close()
            self.stop_queue = None
        LOG.info("Streaming thread terminated")

    def terminate(self):
        self.stop_queue.put("terminate\n")

    def _recv_all(self, sock, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = sock.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def _handle_input_streaming(self):
        # receive data from control VM
        header_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        data_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        header = self._recv_all(self.sock, header_size)
        data = self._recv_all(self.sock, data_size)
        header_data = json.loads(header)

        # add header data for measurement
        if DEBUG.PACKET:
            header_data[Protocol_measurement.JSON_KEY_APP_RECV_TIME] = time.time()

        try:
            if self.data_queue.full() is True:
                self.data_queue.get_nowait()
        except Queue.Empty as e:
            pass
        try:
            self.data_queue.put_nowait((header_data, data))
        except Queue.Full as e:
            pass


class AppProxyBlockingClient(threading.Thread):
    def __init__(self, control_addr, output_queue_list):
        self.stop = threading.Event()
        self.port_number = None
        self.output_queue_list = output_queue_list
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.connect(control_addr)
            LOG.info("Success to connect to %s" % str(control_addr))
        except socket.error as e:
            raise AppProxyError("Failed to connect to %s" % str(control_addr))
        self.stop_queue = multiprocessing.Queue()
        threading.Thread.__init__(self, target=self.run)

    def run(self):
        LOG.info("Start getting data and run program")
        try:
            stopfd = self.stop_queue._reader.fileno()
            socket_fd = self.sock.fileno()
            input_list = [socket_fd, stopfd]
            except_list = [socket_fd, stopfd]
            is_running = True
            while is_running:
                inputready, outputready, exceptready = \
                        select.select(input_list, [], except_list)
                for s in inputready:
                    if s == socket_fd:
                        self._process_input_data()
                    if s == stopfd:
                        is_running = False
                for e in exceptready:
                    is_running = False
        except Exception as e:
            LOG.warning(traceback.format_exc())
            LOG.warning("%s" % str(e))
            LOG.warning("handler raises exception")
            LOG.warning("Server is disconnected unexpectedly")
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        if self.stop_queue is not None:
            self.stop_queue.close()
            self.stop_queue = None
        LOG.info("Streaming thread terminated")

    def terminate(self):
        self.stop_queue.put("terminate\n")

    def _recv_all(self, sock, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = sock.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def _process_input_data(self):
        # receive data from control VM
        header_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        data_size = struct.unpack("!I", self._recv_all(self.sock, 4))[0]
        header = self._recv_all(self.sock, header_size)
        data = self._recv_all(self.sock, data_size)
        header_data = json.loads(header)
        result = self.handle(header_data, data)
        if result is not None:
            header_data[Protocol_client.RESULT_MESSAGE_KEY] = result
            for output_queue in self.output_queue_list:
                output_queue.put(json.dumps(header_data))

        # add header data for measurement
        if DEBUG.PACKET:
            header_data[Protocol_measurement.JSON_KEY_APP_RECV_TIME] = time.time()

    def handle(self, header, data):
        pass


class AppProxyThread(threading.Thread):
    def __init__(self, data_queue, output_queue_list):
        self.data_queue = data_queue
        self.output_queue_list = output_queue_list
        self.stop = threading.Event()
        threading.Thread.__init__(self, target=self.run)

    def run(self):
        while(not self.stop.wait(0.0001)):
            try:
                (header, data) = self.data_queue.get(timeout=0.0001)
            except Queue.Empty as e:
                continue
            if header == None or data == None:
                continue

            result = self.handle(header, data)
            if result is not None:
                header[Protocol_client.RESULT_MESSAGE_KEY] = result
                for output_queue in self.output_queue_list:
                    output_queue.put(json.dumps(header))
        LOG.info("App thread terminated")

    def handle(self, header, data):
        return None

    def terminate(self):
        self.stop.set()


class ResultpublishClient(threading.Thread):
    """
    This client will publish the algorithm result to all TCP server
    listed in publish_addr_list
    """

    def __init__(self, publish_addr_list, result_queue_list):
        self.stop_queue = multiprocessing.Queue()
        self.result_queue_list = result_queue_list
        self.publish_addr_list = list()
        for addr in publish_addr_list:
            (ip, port) = addr.split(":")
            # addr, port, socket, result_data_queue
            self.publish_addr_list.append((ip, int(port), None, None))
        threading.Thread.__init__(self, target=self.publish)

    def update_connection(self):
        for index, (addr, port, app_sock, result_queue) in enumerate(self.publish_addr_list):
            if app_sock is not None:
                continue
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                original_timeout = sock.gettimeout()
                sock.settimeout(1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.connect((addr, port))
                sock.settimeout(original_timeout)
                # update
                result_queue = multiprocessing.Queue()
                self.result_queue_list.append(result_queue)
                self.publish_addr_list[index] = (addr, port, sock, result_queue)
            except socket.error as e:
                LOG.info("Failed to connect to (%s, %d)" % (addr, port))
                pass

    def _get_all_socket_fd(self):
        fd_list = list()
        for (addr, port, app_sock, result_queue) in self.publish_addr_list:
            if app_sock is None:
                continue
            fd_list.append(app_sock.fileno())
        return fd_list

    def _get_all_queue_fd(self):
        fd_list = list()
        for (addr, port, app_sock, result_queue) in self.publish_addr_list:
            if app_sock is None:
                continue
            fd_list.append(result_queue._reader.fileno())
        return fd_list

    def publish(self):
        self.update_connection()
        stopfd = self.stop_queue._reader.fileno()
        input_list = self._get_all_queue_fd() + [stopfd]
        error_list = self._get_all_socket_fd()

        LOG.info("Start publishing data")
        try:
            is_running = True
            while is_running:
                inputready, outputready, exceptready = \
                        select.select(input_list, [], error_list)
                for s in inputready:
                    if s == stopfd:
                        is_running = False
                    else:
                        self._handle_result_output(s)
                for s in exceptready:
                    self._handle_error(s)
        except Exception as e:
            LOG.warning(traceback.format_exc())
            LOG.warning("%s" % str(e))
            LOG.warning("handler raises exception")
            LOG.warning("Server is disconnected unexpectedly")
        LOG.info("Publish thread terminated")
        for index, (addr, port, app_sock, result_queue) in enumerate(self.publish_addr_list):
            if app_sock is None:
                continue
            self.result_queue_list.remove(result_queue)
            app_sock.close()

    def terminate(self):
        self.stop_queue.put("terminate\n")

    def _handle_error(self, socket_fd):
        for index, (addr, port, app_sock, result_queue) in enumerate(self.publish_addr_list):
            if app_sock is None:
                continue
            if app_sock.fileno() == socket_fd:
                self.result_queue_list.remove(result_queue)
                del self.publish_addr_list[index]
                break

    def _handle_result_output(self, output_queue_fd):
        for (addr, port, app_sock, result_queue) in self.publish_addr_list:
            if app_sock is None:
                continue
            if result_queue._reader.fileno() == output_queue_fd:
                try:
                    return_data = result_queue.get_nowait()
                    
                    # measurement header
                    if DEBUG.PACKET:
                        header_data = json.loads(return_data)
                        header_data[Protocol_measurement.JSON_KEY_APP_SENT_TIME] = time.time()
                        return_data = json.dumps(header_data)

                    packet = struct.pack("!I%ds" % len(return_data),
                            len(return_data), return_data)
                    app_sock.sendall(packet)
                    LOG.info("returning result: %s" % return_data)
                except Queue.Empty as e:
                    pass


if __name__ == "__main__":
    LOG.info("Start receiving data")
    try:
        control_addr = ("128.2.210.197", 10101)
        client = AppProxyStreamingClient(control_addr)
        client.start()
        client.isDaemon = True
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        LOG.info("user exits")
        client.terminate()
    except Exception as e:
        client.terminate()

