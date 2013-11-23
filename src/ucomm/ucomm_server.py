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
sys.path.insert(0, "../")
from control.protocol import Protocol_client
from control.protocol import Protocol_measurement
from control.config import ServiceMeta
from urlparse import urlparse

import httplib
import urllib2
import time
import SocketServer
import socket
import select
import traceback
import struct
import json
import Queue
import threading
import pprint
from optparse import OptionParser

from control.config import ServiceMeta as SERVICE_META
from control.upnp_client import UPnPClient


class tempLOG(object):
    def info(self, data):
        sys.stdout.write("INFO\t" + data + "\n")
    def warning(self, data):
        sys.stdout.write("warning\t" + data + "\n")
    def debug(self, data):
        sys.stdout.write("DEBUG\t" + data + "\n")
    def error(self, data):
        sys.stderr.write("ERROR\t" + data + "\n")


LOG = tempLOG()
upnp_client = UPnPClient()
output_queue = Queue.Queue()


class UCommConst(object):
    RESULT_RECEIVE_PORT     =   10120


class UCommError(Exception):
    pass


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


def get_ip(iface = 'eth0'):
    import fcntl
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


def put(url, json_string):
    end_point = urlparse("%s" % url)
    params = json.dumps(json_string)
    headers = {"Content-type": "application/json"}

    conn = httplib.HTTPConnection(end_point[1])
    conn.request("PUT", "%s" % end_point[2], params, headers)
    response = conn.getresponse()
    data = response.read()
    dd = json.loads(data)
    if dd.get("return", None) != "success":
        msg = "Cannot register UCOMM server\n%s", str(dd)
        raise UCommError(msg)
    print dd
    conn.close()


def register_ucomm(argv):
    settings, args = process_command_line(sys.argv[1:])
    service_list = None
    directory_url = None
    if settings.address is None:
        upnp_client.start()
        upnp_client.join()
        directory_url = "http://%s:%d/" % \
                    (upnp_client.http_ip_addr, upnp_client.http_port_number)
        service_list = upnp_client.service_list
    else:
        ip_addr, port = settings.address.split(":", 1)
        port = int(port)
        directory_url = "http://%s:%d/" % (ip_addr, port)
        meta_stream = urllib2.urlopen(directory_url)
        meta_raw = meta_stream.read()
        service_list = json.loads(meta_raw)
    pstr = pprint.pformat(service_list)
    json_info = {
        ServiceMeta.RESULT_RETURN_SERVER_LIST: "%s:%d" % (get_ip(), UCommConst.RESULT_RECEIVE_PORT),
        }
    put(directory_url, json_info)
    LOG.info("Gabriel Server :")
    LOG.info(pstr)
    return service_list


class ResultForwardingClient(threading.Thread):
    """
    This client will forward all the result received from the client
    """

    def __init__(self, control_address, output_queue):
        self.stop = threading.Event()
        self.output_queue = output_queue
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.connect(control_address)

        self.previous_sent_time = time.time()
        self.previous_sent_data = ""
        threading.Thread.__init__(self, target=self.forward)

    def forward(self):
        output_list = [self.sock.fileno()]
        error_list = [self.sock.fileno()]

        LOG.info("Start forwardning data")
        try:
            while(not self.stop.wait(0.0001)):
                inputready, outputready, exceptready = \
                        select.select([], output_list, error_list, 0)
                for s in outputready:
                    self._handle_result_output()
                for s in exceptready:
                    break
                time.sleep(0.001)
        except Exception as e:
            LOG.warning(traceback.format_exc())
            LOG.warning("%s" % str(e))
            LOG.debug("Result Forwading thread terminated")
        self.terminate()

    def terminate(self):
        self.stop.set()
        if self.sock is not None:
            self.sock.close()

    @staticmethod
    def _post_header_process(header_message):
        header_json = json.loads(header_message)
        frame_id = header_json.get(Protocol_client.FRAME_MESSAGE_KEY, None)
        if frame_id is not None:
            header_json[Protocol_client.RESULT_ID_MESSAGE_KEY] = frame_id
            del header_json[Protocol_client.FRAME_MESSAGE_KEY]
        return json.dumps(frame_id)

    def _handle_result_output(self):
        while self.output_queue.empty() is False:
            try:
                return_data = self.output_queue.get_nowait()

                # ignore the result if same output is sent within 1 sec
                return_json = json.loads(return_data)
                result_str = return_json.get(Protocol_client.RESULT_MESSAGE_KEY, None)
                if result_str == None:
                    continue
                if self.previous_sent_data.lower() == result_str.lower():
                    time_diff = time.time() - self.previous_sent_time 
                    if time_diff < 2:
                        LOG.info("Identical result (%s) is ignored" % result_str)
                        return_json.pop(Protocol_client.RESULT_MESSAGE_KEY)

                # add header data for measurement
                #return_json[Protocol_measurement.JSON_KEY_UCOMM_SENT_TIME] = time.time()

                output = json.dumps(return_json)
                packet = struct.pack("!I%ds" % len(output),
                        len(output), output)
                self.sock.sendall(packet)
                self.previous_sent_time = time.time()
                self.previous_sent_data = result_str

                LOG.info("forward the result: %s" % output)
            except Queue.Empty as e:
                pass


class UCommServerHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(UCommServerHandler, self).setup()

    def _recv_all(self, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = self.request.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise UCommError("Socket is closed")
            data += tmp_data
        return data

    def handle(self):
        try:
            LOG.info("new Offlaoding Engine is connected")
            socket_fd = self.request.fileno()
            input_list = [socket_fd]
            while True:
                inputready, outputready, exceptready = \
                        select.select(input_list, [], [], 100)
                for insocket in inputready:
                    if insocket == socket_fd:
                        self._handle_input_stream()
                time.sleep(0.00001)
        except Exception as e:
            #LOG.debug(traceback.format_exc())
            LOG.debug("%s" % str(e))
            LOG.info("Offloading engine is disconnected")
            self.terminate()

    def _handle_input_stream(self):
        global output_queue
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        output_queue.put(header_data)


class UCommServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    stopped = False

    def __init__(self, port, handler):
        server_address = ('0.0.0.0', port)
        self.allow_reuse_address = True
        self.handler = handler
        try:
            SocketServer.TCPServer.__init__(self, server_address, handler)
        except socket.error as e:
            sys.stderr.write(str(e))
            sys.stderr.write("Check IP/Port : %s\n" % (str(server_address)))
            sys.exit(1)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        LOG.info("* UCOMM server configuration")
        LOG.info(" - Open TCP Server at %s" % (str(server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(
                    socket.IPPROTO_TCP,
                    socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def serve_forever(self):
        while not self.stopped:
            self.handle_request()

    def handle_error(self, request, client_address):
        pass

    def terminate(self):
        self.server_close()
        self.stopped = True
        
        if self.socket != -1:
            self.socket.close()
        LOG.info("[TERMINATE] Finish ucomm server")


def main():
    global output_queue

    try:
        service_list = register_ucomm(sys.argv)
    except Exception as e:
        LOG.info(str(e))
        LOG.info("failed to register UCOMM to the control")
        sys.exit(1)
    control_vm_ip = service_list.get(SERVICE_META.UCOMM_COMMUNICATE_ADDRESS)
    control_vm_port = service_list.get(SERVICE_META.UCOMM_COMMUNICATE_PORT)

    # result pub/sub
    try:
        result_forward = None
        LOG.info("connecting to %s:%d" % (control_vm_ip, control_vm_port))
        result_forward = ResultForwardingClient((control_vm_ip, control_vm_port), \
                output_queue)
        result_forward.isDaemon = True
    except socket.error as e:
        # do not proceed if cannot connect to control VM
        LOG.info("Failed to connect to Control server (%s:%d)" % \
                (control_vm_ip, control_vm_port))
        if result_forward is not None:
            result_forward.terminate()
        return 2

    exit_status = 1
    ucomm_server = None
    ucomm_server = UCommServer(UCommConst.RESULT_RECEIVE_PORT, UCommServerHandler)
    ucomm_thread = threading.Thread(target=ucomm_server.serve_forever)
    ucomm_thread.daemon = True
    try:
        result_forward.start()
        ucomm_thread.start()
        while True:
            time.sleep(100)
    except Exception as e:
        sys.stderr.write(str(e))
        exit_status = 1
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        exit_status = 0
    finally:
        if ucomm_server is not None:
            ucomm_server.terminate()
        if result_forward is not None:
            result_forward.terminate()
    return exit_status


if __name__ == '__main__':
    ret = main()
    sys.exit(ret)
