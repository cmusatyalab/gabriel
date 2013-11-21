#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Zhuo Chen <zhuoc@cs.cmu.edu>
#           Kiryong Ha <krha@cmu.edu>
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
import socket
import select
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

from motion_classifier import extract_feature
from motion_classifier import classify

MASTER_PORT = 8747
MASTER_TAG = "Master Node: "
SLAVE_TAG = "Slave Node: "

class MasterProxy(threading.Thread):
    def __init__(self, data_queue, feature_queue):
        self.data_queue = data_queue
        self.feature_queue = feature_queue
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
        self.server.bind(("", MASTER_PORT)) 
        self.server.listen(10) # expect less then 10 connections... 
        self.last_image_parts = None
        self.connection_num = 0
        self.partial_data_queues = []
        self.partial_feature_queues = []
        self.queue_mapping = {}
        self.stop = threading.Event()
        threading.Thread.__init__(self, target=self.run)

    def run(self):
        input_list = [server]
        output_list = []
        error_list = []

        LOG.info(MASTER_TAG + "Master node started")
        try:
            while(not self.stop.wait(0.001)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, error_list, 0.1) # zhuoc: do we need a timeout here?
                for s in inputready:
                    if s == self.server:
                        client, address = server.accept() 
                        input_list.append(client)
                        output_list.append(client)
                        error_list.append(client)
                        queue = Queue.Queue(10)
                        self.partial_data_queues.append(queue)
                        queue = Queue.Queue(10)
                        self.partial_feature_queues.append(queue)
                        self.queue_mapping[client] = self.connection_num
                        self.connection_num += 1
                    else:
                        self.receive_partial_image(s)
                for s in outputready:
                    self._send_partial_image(s)
                for s in exceptready:
                    pass
                time.sleep(0.001)
        except Exception as e:
            LOG.warning(MASTER_TAG + traceback.format_exc())
            LOG.warning(MASTER_TAG + "%s" % str(e))
            LOG.warning(MASTER_TAG + "handler raises exception")
            LOG.warning(MASTER_TAG + "Server is disconnected unexpectedly")
        LOG.debug(MASTER_TAG + "Master thread terminated")

    def _chop_image(image):
        # TODO
        return None

    def _send_partial_image(self, sock):
        # send image pairs
        try:
            data = self.partial_data_queues[self.queue_mapping[sock]].get_nowaite()
            packet = struct.pack("!I%ds" % len(data), len(data), data)
            sock.sendall(packet)
            return
        except Queue.Empty as e:
            pass

        # check if any queue is full
        for queue in self.partial_data_queues:
            if queue.full():
                return
        
        # check new image
        try:
            (header, new_image) = self.data_queue.get_nowait()
        except Queue.Empty as e:
            return
        # chop image and put image pairs to different queues
        new_image_parts = self._chop_image(new_image)
        if self.last_image_parts:
            image_pairs = zip(self.last_image_parts, new_image_parts)
        else:
            self.last_image_parts = new_image_parts
            return
        self.last_image_parts = new_image_parts
        try:
            for idx, queue in enumerate(self.partial_data_queues):
                queue.put_nowait(image_pairs[idx])
        except Queue.Full as e:
            LOG.warning(MASTER_TAG + "Image pair queue shouldn't be full")
            pass

        return None

    @staticmethod
    def _recv_all(socket, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = socket.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def _receive_partial_image(self, sock):
        data_size = struct.unpack("!I", self._recv_all(sock, 4))[0]
        data = self._recv_all(sock, data_size)
        #TODO transform data to right location

        # check if features for one whole image pair are received
        for queue in self.partial_feature_queues:
            if queue.empty():
                return
        # integrate features for one image pair an put into feature queue
        try:
            features = []
            for queue in self.partial_feature_queues:
                feature = queue.get_nowait()
            features += feature
            self.feature_queue.put_nowait(features)
        except Queue.Empty as e:
            LOG.warning(MASTER_TAG + "Partial feature queue shouldn't be empty")
        except Queue.Full as e:
            LOG.warning(MASTER_TAG + "Feature queue shouldn't be full")

    def terminate(self):
        self.stop.set()

class MasterProcessing(threading.Thread):
    def __init__(self, feature_queue, output_queue_list, detect_period):
        self.feature_queue = feature_queue
        self.output_queue_list = output_queue_list
        self.stop = threading.Event()
        self.detect_period = detect_period
        self.window_len = 90
        self.feature_list = []
        threading.Thread.__init__(self, target=self.run)

    def run(self):
        while(not self.stop.wait(0.001)):
            try:
                feature = self.data_queue.get_nowait()
            except Queue.Empty as e:
                continue

            self.feature_list.append(feature)
            if len(feature_list) == self.window_len:
                # TODO classify motion
                motion_classifier = classify(feature_list)
                for i in xrange(detect_period):
                    del feature_list[0]

            result = motion_classifier
            if result is not None:
                return_message = dict()
                return_message[Protocol_client.RESULT_MESSAGE_KEY] = result
                frame_id = header.get(Protocol_client.FRAME_MESSAGE_KEY, None)
                if frame_id is not None:
                    return_message[Protocol_client.FRAME_MESSAGE_KEY] = frame_id
                for output_queue in self.output_queue_list:
                    output_queue.put(json.dumps(return_message))
        LOG.debug("App thread terminated")

    def terminate(self):
        self.stop.set()

class SlaveProxy(AppProxyThread):
    def __init__(self, app_addr, data_queue, output_queue):
        super(SlaveProxy, self).__init__(data_queue, output_queue)
        self.app_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.app_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.app_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.app_sock.connect(app_addr)
        self.result_queue = output_queue

    def terminate(self):
        super(AppProxyThread, self).terminate()
        if self.app_sock is not None:
            self.app_sock.close()

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
    service_list = get_service_list()
    video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
    video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
    return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

    # master node discovery, not implemented yet
    master_node_ip = service_list.get(SERVICE_META.MOTION_CLASSIFIER_MASTER_IP, "")
    master_node_port = service_list.get(SERVICE_META.MOTION_CLASSIFIER_MASTER_PORT, -1)
    is_master = False
    if not master_node_ip:
        register_as_master() # not implemented
        is_master = True

    # Master proxy
    if is_master:
        image_queue = Queue.Queue(1)
        feature_queue = Queue.Queue(3)
        output_queue_list = list()
        master_streaming = AppProxyStreamingClient((video_ip, video_port), image_queue)
        master_streaming.start()
        master_streaming.isDaemon = True
        master_proxy = MasterProxy(image_queue, feature_queue)
        master_proxy.start()
        master_proxy.isDaemon = True
        master_processing = MasterProcessing(feature_queue, output_queue_list)
        master_processing.start()
        master_processing.isDaemon = True
        result_pub = ResultpublishClient(return_addresses, output_queue_list) # this is where output_queues are created according to each return_address
        result_pub.start()
        result_pub.isDaemon = True

    # Slave proxy
    slave_queue = Queue.Queue(1)
    slave_streaming = AppProxyStreamingClient((master_node_ip, master_node_port), slave_queue)
    slave_streaming.start()
    slave_streaming.isDaemon = True
    slave_proxy = SlaveProxy(slave_queue, output_queue_list)
    slave_proxy.start()
    slave_proxy.isDaemon = True

    LOG.info(MASTER_TAG + "Start receiving data\n")
    LOG.info(SLAVE_TAG + "Start receiving data\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
    except Exception as e:
        pass
    finally:
        client.terminate()
        app_thread.terminate()
        result_pub.terminate()

