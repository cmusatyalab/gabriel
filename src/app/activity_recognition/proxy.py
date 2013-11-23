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
import sys
sys.path.insert(0, "../common")
import socket
import select
import struct
import sys
import Queue
import time
import cv2
import numpy as np
from cStringIO import StringIO

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
        self.slave_num = 1
        self.w_parts = 1
        self.h_parts = 1
        self.partial_data_queues = []
        self.partial_feature_queues = []
        self.crop_config_queue = Queue.Queue()
        self.round_robin_counter = 0
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
                        select.select(input_list, output_list, error_list, 0.1) 
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
                        if self.connection_num >= self.slave_num * 2:
                            self.slave_num *= 2
                            is self.h_parts > self.w_parts:
                                self.w_parts = self.h_parts
                            else:
                                self.h_parts *= 2
                    else:
                        self._receive_partial_image(s)
                for s in outputready:
                    self._send_partial_image(s)
                for s in exceptready:
                    pass
        except Exception as e:
            LOG.warning(MASTER_TAG + traceback.format_exc())
            LOG.warning(MASTER_TAG + "%s" % str(e))
            LOG.warning(MASTER_TAG + "handler raises exception")
            LOG.warning(MASTER_TAG + "Server is disconnected unexpectedly")
        LOG.debug(MASTER_TAG + "Master thread terminated")

    def _crop_image(image):
        img_array = np.asarray(bytearray(image), dtype=np.uint8)
        cv_image = cv2.imdecode(img_array, -1)
        height, width, depth = cv_image.shape
        h_block = height / h_parts
        w_block = width / w_parts
        overlap = 10

        image_parts = []
        crop_config = []
        for i in xrange(self.h_parts):
            h_min = h_block * i
            h_max = h_min + h_block
            if h_min > 0:
                h_min -= overlap
            if h_max < height:
                h_max += overlap
            for j in xrange(self.w_parts):
                w_min = w_block * j
                w_max = w_min + w_block
                if w_min > 0:
                    w_min -= overlap
                if w_max < height:
                    w_max += overlap
                image_part = cv_image[h_min:h_max, w_min:w_max]
                image_parts.append(image_parts)
                crop_config.append((h_min, h_max, w_min, w_max))

        return image_parts, crop_config

    def _round_robin(l, counter):
        new_l = l[counter:]
        new_l += l[:counter]
        return new_l

    def _send_partial_image(self, sock):
        # send image pairs
        try:
            images = self.partial_data_queues[self.queue_mapping[sock]].get_nowaite()
            data = json.dumps(images)
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
        new_image_parts, crop_config = self._crop_image(new_image)
        if not self.last_image_parts:
            self.last_image_parts = new_image_parts
            return
        image_pairs = zip(self.last_image_parts, new_image_parts)
        self.last_image_parts = new_image_parts
        image_pairs = _round_robin(image_pairs, self.round_robin_counter)
        crop_config = _round_robin(crop_config, self.round_robin_counter)
        self.round_robin_counter += 1
        try:
            for idx in xrange(self.slave_num):
                self.partial_data_queues[idx].put_nowait(image_pairs[idx])
            self.crop_config_queue.put_nowait((self.slave_num, crop_config))
        except Queue.Full as e:
            LOG.warning(MASTER_TAG + "Image pair queue shouldn't be full")

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

    def _uncrop_feature(feature, crop_config)
        h_min, h_max, w_min, w_max = crop_config
        new_feature = []
        for a_feature in feature:
            local_x, local_y, centers = a_feature.split(' ', 2)
            global_x = float(local_x) + w_min
            global_y = float(local_y) + y_min
            new_feature.append("%f %f " % (global_x, global_y) + centers)
        return new_feature

    def _receive_partial_image(self, sock):
        data_size = struct.unpack("!I", self._recv_all(sock, 4))[0]
        data = self._recv_all(sock, data_size)
        feature = json.loads(data)
        try:
            self.partial_feature_queues[self.queue_mapping[sock]].put_nowait(feature)
        except Queue.Full as e:
            LOG.warning(MASTER_TAG + "Partial feature queue shouldn't be full")

        # check if features for one whole image pair are received
        for queue in self.partial_feature_queues:
            if queue.empty():
                return
        # integrate features for one image pair an put into feature queue
        try:
            n_parts, crop_config = self.crop_config_queue.get_nowaite()
        except Queue.Empty as e:
            LOG.warning(MASTER_TAG + "Crop config queue shouldn't be empty")
        try:
            features = []
            for idx in xrange(n_parts):
                feature = self.partial_feature_queues[idx].get_nowait()
                feature = self._uncrop_feature(feature, crop_config[idx])
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
                result = classify(feature_list)
                for i in xrange(detect_period):
                    del feature_list[0]

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

class SlaveProxy(threading.Thread):
    def __init__(self, master_addr):
        self.stop = threading.Event()
        try:
            self.master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.master_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.master_sock.connect(master_addr)
        except socket.error as e:
            LOG.warning(SLAVE_TAG + "Failed to connect to %s" % str(master_addr))
        threading.Thread.__init__(self, target=self.run)

    @staticmethod
    def _recv_all(socket, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = socket.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def run(self):
        LOG.info(SLAVE_TAG + "Start getting data from master node")
        socket_fd = self.master_sock.fileno()
        input_list = [socket_fd]
        try:
            while(not self.stop.wait(0.001)):
                inputready, outputready, exceptready = \
                        select.select(input_list, [], [], 0)
                for s in inputready:
                    if s == socket_fd:
                        data_size = struct.unpack("!I", self._recv_all(self.master_sock, 4))[0]
                        data = self._recv_all(self.master_sock, data_size)
                        data = json.loads(data)

                        result = self.handle(data) # results is a list of lines

                        data_back = json.dumps(result)
                        packet = struct.pack("!I%ds" % len(data_back), len(data_back), data_back)
                        self.master_sock.sendall(packet)
        except Exception as e:
            LOG.warning(traceback.format_exc())
            LOG.warning("%s" % str(e))
            LOG.warning("handler raises exception")
            LOG.warning("Server is disconnected unexpectedly")
        self.master_sock.close()
        LOG.debug("Proxy thread terminated")

    def terminate(self):
        self.stop.set()
        if self.master_sock is not None:
            self.master_sock.close()
    
    def handle(self, images):
        # receive data from control VM
        LOG.info(SLAVE_TAG + "receiving new image pair")

        # extract feature 
        result = extract_feature(images)
        
        return result

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
        master_processing = MasterProcessing(feature_queue, output_queue_list, )
        master_processing.start()
        master_processing.isDaemon = True
        result_pub = ResultpublishClient(return_addresses, output_queue_list) # this is where output_queues are created according to each return_address
        result_pub.start()
        result_pub.isDaemon = True

        LOG.info(MASTER_TAG + "Start receiving data\n")

    # Slave proxy
    #slave_queue = Queue.Queue(1)
    #slave_streaming = AppProxyStreamingClient((master_node_ip, master_node_port), slave_queue)
    #slave_streaming.start()
    #slave_streaming.isDaemon = True
    slave_proxy = SlaveProxy((master_node_ip, master_node_port))
    slave_proxy.start()
    slave_proxy.isDaemon = True

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

