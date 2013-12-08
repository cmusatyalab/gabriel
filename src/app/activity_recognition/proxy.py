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
sys.path.insert(0, "../../")
import socket
import select
import struct
import os
import random
import Queue
import time
import traceback
import cv2
import threading
from urlparse import urlparse
import json
import httplib
import urllib2
import numpy as np
from cStringIO import StringIO

from control.protocol import Protocol_client

from launcher import AppLauncher
from app_proxy import AppProxyError
from app_proxy import AppProxyStreamingClient
from app_proxy import AppProxyThread
from app_proxy import ResultpublishClient
from app_proxy import get_service_list
from app_proxy import SERVICE_META
from app_proxy import LOG

from motion_classifier import extract_feature
from motion_classifier import compute_hist
from motion_classifier import compute_confidence

TXYC_PATH = "bin/txyc"
TXYC_ARGS = ['cluster_centers/centers_mosift_MOTION_1024', '1024', 'mosift', 'MOTION']
TXYC_PORT = 8748
MASTER_PORT = 8747
MASTER_CLASSIFICATION_PORT = 8749
MASTER_TAG = "Master Node: "
SLAVE_TAG = "Slave Node: "

model_names = ["SayHi", "Clapping", "TurnAround", "Squat"]
MESSAGES = ["Someone is waving to you.", "Someone is clapping.", "Someone is turning around.", "Someone just squated."]

class MasterProxy(threading.Thread):
    def __init__(self, data_queue, feature_queue):
        self.data_queue = data_queue
        self.feature_queue = feature_queue
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.server.bind(("", MASTER_PORT)) 
        self.server.listen(10) # expect less then 10 connections... 
        self.last_image_parts = None
        self.connection_num = 0
        self.slave_num = 1
        self.w_parts = 1
        self.h_parts = 1
        self.partial_data_queues = []
        self.partial_feature_queues = []
        self.split_config_queue = Queue.Queue()
        self.tokens = []
        self.frame_id_queues = []
        self.round_robin_counter = 0
        self.queue_mapping = {}
        self.stop = threading.Event()
        threading.Thread.__init__(self, target=self.run)

    def run(self):
        input_list = [self.server]
        output_list = []
        error_list = []

        LOG.info(MASTER_TAG + "Master node started")
        try:
            while(not self.stop.wait(0.001)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, error_list, 0.001) 
                if self.last_image_parts: # whenever get a new image, get a new image pair
                    self._get_new_image()
                for s in inputready:
                    if s == self.server:
                        client, address = self.server.accept() 
                        input_list.append(client)
                        output_list.append(client)
                        error_list.append(client)
                        queue = Queue.Queue(1)
                        self.partial_data_queues.append(queue)
                        queue = Queue.Queue(2)
                        self.partial_feature_queues.append(queue)
                        self.tokens.append(True)
                        queue = []
                        self.frame_id_queues.append(queue) # this is used only for debug
                        self.queue_mapping[client] = self.connection_num
                        self.connection_num += 1
                        if self.connection_num >= self.slave_num * 2:
                            self.slave_num *= 2
                            if self.h_parts > self.w_parts:
                                self.w_parts = self.h_parts
                            else:
                                self.h_parts *= 2
                    else:
                        self._receive_partial_feature(s)
                for s in outputready:
                    #if self.tokens[self.queue_mapping[s]]:
                    self._send_partial_image(s)
                for s in exceptready:
                    pass
        except Exception as e:
            LOG.warning(MASTER_TAG + traceback.format_exc())
            LOG.warning(MASTER_TAG + "%s" % str(e))
            LOG.warning(MASTER_TAG + "handler raises exception")
            LOG.warning(MASTER_TAG + "Server is disconnected unexpectedly")
        LOG.debug(MASTER_TAG + "Master thread terminated")

    def _resize_and_split_image(self, image):
        img_array = np.asarray(bytearray(image), dtype=np.uint8)
        cv_image = cv2.imdecode(img_array, -1)
        cv_image = cv2.resize(cv_image, (160, 120))
        height, width, depth = cv_image.shape
        h_block = height / self.h_parts
        w_block = width / self.w_parts
        overlap = 0

        image_parts = []
        split_config = []
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
                # convert image_part to bytearrays
                tmp_file_name = "tmp/part%d.jpg" % (i * self.w_parts + j)
                cv2.imwrite(tmp_file_name, image_part)
                with open(tmp_file_name) as f:
                    image_part = f.read()
                os.remove(tmp_file_name)
                image_parts.append(image_part)
                split_config.append((h_min, h_max, w_min, w_max))

        return image_parts, split_config

    @staticmethod
    def _round_robin(l, counter):
        new_l = l[counter:]
        new_l += l[:counter]
        return new_l

    def _get_new_image(self):
        # check new image
        try:
            (header, new_image) = self.data_queue.get_nowait()
        except Queue.Empty as e:
            return
        if self.slave_num < 4:
            LOG.warning(MASTER_TAG + "Discard incoming images because not all slave nodes are ready")
            return
        frame_id = header.get(Protocol_client.FRAME_MESSAGE_KEY, None)
        # chop image and put image pairs to different queues
        new_image_parts, split_config = self._resize_and_split_image(new_image)
        if not self.last_image_parts:
            self.last_image_parts = new_image_parts
            return
        LOG.info(MASTER_TAG + "Got frame %d from input queue at time %f" % (frame_id, time.time()))
        image_pairs = zip(self.last_image_parts, new_image_parts)
        self.last_image_parts = None
        image_pairs = self._round_robin(image_pairs, self.round_robin_counter)
        split_config = self._round_robin(split_config, self.round_robin_counter)
        self.round_robin_counter = (self.round_robin_counter + 1) % self.slave_num
        try:
            for idx in xrange(self.slave_num):
                self.partial_data_queues[idx].put_nowait(image_pairs[idx])
                self.frame_id_queues[idx].append(frame_id)
            self.split_config_queue.put_nowait((frame_id, self.slave_num, split_config))
        except Queue.Full as e:
            LOG.warning(MASTER_TAG + "!!!!!!!!!!!!!!!!!!Image pair queue shouldn't be full!!!!!!!!!!!!!!!!!")

    def _send_partial_image(self, sock):
        # send image pairs
        try:
            images = self.partial_data_queues[self.queue_mapping[sock]].get_nowait()
            self.tokens[self.queue_mapping[sock]] = False
            data = images[0] + images[1]
            header = {"images_length" : [len(images[0])],
                      "frame_id" : self.frame_id_queues[self.queue_mapping[sock]][-1]}
            header_json = json.dumps(header)
            packet = struct.pack("!I%ds" % len(header_json), len(header_json), header_json)
            sock.sendall(packet)
            packet = struct.pack("!I%ds" % len(data), len(data), data)
            sock.sendall(packet)
            return
        except Queue.Empty as e:
            pass

        # check if any queue is full
        #for queue in self.partial_data_queues:
        #    if queue.full():
        #        #if random.random() < 0.01:
        #        LOG.warning(MASTER_TAG + "Partial data queue full, slave nodes are seriously inbalanced")
        #        return
        for token in self.tokens:
            if not token:
                return
        self._get_new_image()

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

    @staticmethod
    def _integrate_feature(feature, split_config):
        h_min, h_max, w_min, w_max = split_config
        new_feature = []
        for a_feature in feature:
            local_x, local_y, centers = a_feature.split(' ', 2)
            global_x = float(local_x) + w_min
            global_y = float(local_y) + h_min
            new_feature.append("%f %f " % (global_x, global_y) + centers)
        return new_feature

    def _receive_partial_feature(self, sock):
        data_size = struct.unpack("!I", self._recv_all(sock, 4))[0]
        data = self._recv_all(sock, data_size)
        feature = json.loads(data)
        try:
            self.partial_feature_queues[self.queue_mapping[sock]].put_nowait(feature)
            self.tokens[self.queue_mapping[sock]] = True
            del self.frame_id_queues[self.queue_mapping[sock]][0]
        except Queue.Full as e:
            LOG.warning(MASTER_TAG + "Partial feature queue shouldn't be full")
            for idx in xrange(self.slave_num):
                print self.frame_id_queues[idx]
            return

        # check if features for one whole image pair are received
        for idx in xrange(self.slave_num):
            #if self.partial_feature_queues[idx].empty():
            if not self.tokens[idx]:
                return
        # integrate features for one image pair and put into feature queue
        try:
            frame_id, n_parts, split_config = self.split_config_queue.get_nowait()
        except Queue.Empty as e:
            LOG.warning(MASTER_TAG + "Split config queue shouldn't be empty")
        try:
            features = []
            for idx in xrange(n_parts):
                feature = self.partial_feature_queues[idx].get_nowait()
                feature = self._integrate_feature(feature, split_config[idx])
                features += feature
            self.feature_queue.put_nowait((frame_id, features))
        except Queue.Empty as e:
            LOG.warning(MASTER_TAG + "Partial feature queue shouldn't be empty")
        except Queue.Full as e:
            LOG.warning(MASTER_TAG + "Feature queue shouldn't be full")

    def terminate(self):
        self.stop.set()

class MasterClassification(threading.Thread):
    def __init__(self, feature_queue, output_queue_list, detect_period):
        self.feature_queue = feature_queue
        self.output_queue_list = output_queue_list
        self.stop = threading.Event()
        self.detect_period = detect_period
        self.window_len = 30
        self.feature_list = []
        self.tokens = []
        self.confidences = [0, 0, 0, 0]
        self.frame_id = None
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.server.bind(("", MASTER_CLASSIFICATION_PORT))
        self.server.listen(10) # expect less then 10 connections...
        self.queue_mapping = {}
        self.connection_num = 0
        self.slave_num = 1
        threading.Thread.__init__(self, target=self.run)

    def run(self):
        input_list = [self.server]
        output_list = []
        error_list = []

        LOG.info(MASTER_TAG + "Master node started")
        try:
            while(not self.stop.wait(0.001)):
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, error_list, 0.001)
                for s in inputready:
                    if s == self.server:
                        print "client connected"
                        client, address = self.server.accept()
                        input_list.append(client)
                        output_list.append(client)
                        error_list.append(client)
                        self.tokens.append(True)
                        self.queue_mapping[client] = self.connection_num
                        self.connection_num += 1
                        if self.connection_num >= self.slave_num * 2:
                            self.slave_num *= 2
                    else:
                        self._receive(s)
                self._try_classify(output_list)
        except Exception as e:
            LOG.warning(MASTER_TAG + traceback.format_exc())
            LOG.warning(MASTER_TAG + "%s" % str(e))
            LOG.warning(MASTER_TAG + "handler raises exception")
            LOG.warning(MASTER_TAG + "Server is disconnected unexpectedly")
        LOG.debug(MASTER_TAG + "Master thread terminated")

    @staticmethod
    def _recv_all(socket, recv_size):
        data = ''
        while len(data) < recv_size:
            tmp_data = socket.recv(recv_size - len(data))
            if tmp_data == None or len(tmp_data) == 0:
                raise AppProxyError("Socket is closed")
            data += tmp_data
        return data

    def _try_classify(self, output_list):
        try:
            curr_frame_id, features = self.feature_queue.get_nowait()
        except Queue.Empty as e:
            return

        LOG.info(MASTER_TAG + "Got feature back of frame %d at time %f" % (curr_frame_id, time.time()))
        #result = None
        self.feature_list.append(features)
        if len(self.feature_list) == self.window_len:
            self.frame_id = curr_frame_id

            #feature_vec = compute_hist(self.feature_list)
            #feature_vec = list(feature_vec.items())
            if self.slave_num == 1:
                data = ((0,1,2,3), self.feature_list)
                data_json = json.dumps(data)
                packet = struct.pack("!I%ds" % len(data_json), len(data_json), data_json)
                output_list[0].sendall(packet)
            elif self.slave_num >= 4:
                for model_idx in xrange(4):
                    if not self.tokens[model_idx]:
                        sys.exit()
                    data = ((model_idx, ), self.feature_list)
                    data_json = json.dumps(data)
                    packet = struct.pack("!I%ds" % len(data_json), len(data_json), data_json)
                    output_list[model_idx].sendall(packet)
                    self.tokens[model_idx] = False
            else:
                for model_idx in xrange(2):
                    if not self.tokens[model_idx]:
                        sys.exit()
                    data = ((model_idx * 2, model_idx * 2 + 1), self.feature_list)
                    data_json = json.dumps(data)
                    packet = struct.pack("!I%ds" % len(data_json), len(data_json), data_json)
                    output_list[model_idx].sendall(packet)
                    self.tokens[model_idx] = False
            LOG.info(MASTER_TAG + "Sent all features at time %f" % time.time())

    def _receive(self, sock):
        data_size = struct.unpack("!I", self._recv_all(sock, 4))[0]
        data = self._recv_all(sock, data_size)
        if self.slave_num == 2:
            confidence = json.loads(data)
            idx = self.queue_mapping[sock]
            self.confidences[idx * 2] = confidence[0]
            self.confidences[idx * 2 + 1] = confidence[1]
        elif self.slave_num == 4:
            self.confidences[self.queue_mapping[sock]] = json.loads(data)[0]
        else:
            self.confidences = json.loads(data)
        self.tokens[self.queue_mapping[sock]] = True

        for token in self.tokens:
            if not token:
                return

        print self.confidences
        LOG.info(MASTER_TAG + "Got all confidences back at time %f" % time.time())
        result = None

        max_score = 0
        model_idx = -1
        for idx in xrange(4):
            if self.confidences[idx] > max_score:
                max_score = self.confidences[idx]
                model_idx = idx
        model_name = model_names[model_idx]
        if max_score > 0.5: # Activity is detected
            print
            print "ACTIVITY DETECTED: %s!" % model_name
            print "Confidence score: %f" % max_score
            print

            result = MESSAGES[model_idx]
        else:
            print "\nMax confidence score: %f, activity is: %s\n" % (max_score, model_name)
            result = "nothing"

        for i in xrange(self.detect_period):
            del self.feature_list[0]

        LOG.info(MASTER_TAG + "Classification done at time %f" % (time.time()))

        if result is not None:
            print result
            return_message = dict()
            return_message[Protocol_client.RESULT_MESSAGE_KEY] = result
            if self.frame_id is not None:
                return_message[Protocol_client.FRAME_MESSAGE_KEY] = self.frame_id
            for output_queue in self.output_queue_list:
                output_queue.put(json.dumps(return_message))
    
    def terminate(self):
        self.stop.set()

class SlaveProxy(threading.Thread):
    def __init__(self, master_addr, txyc_addr, is_print = True):
        self.stop = threading.Event()
        self.is_print = is_print
        try:
            self.master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.master_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.master_sock.connect(master_addr)
        except socket.error as e:
            LOG.warning(SLAVE_TAG + "Failed to connect to %s" % str(master_addr))
        try:
            self.txyc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.txyc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.txyc_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.txyc_sock.connect(txyc_addr)
        except socket.error as e:
            LOG.warning(SLAVE_TAG + "Failed to connect to %s" % str(txyc_addr))
        threading.Thread.__init__(self, target=self.run)

    def _log(self, message):
        if self.is_print:
            LOG.info(SLAVE_TAG + message)

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
                        self._log(SLAVE_TAG + "Start receiving new image pair at %f" % time.time())
                        header_size = struct.unpack("!I", self._recv_all(self.master_sock, 4))[0]
                        header_json = self._recv_all(self.master_sock, header_size)
                        header = json.loads(header_json)
                        data_size = struct.unpack("!I", self._recv_all(self.master_sock, 4))[0]
                        data = self._recv_all(self.master_sock, data_size)
                        self._log("Finished receiving image pair at %f" % time.time())
                        frame_id = header.get("frame_id")
                        images = []
                        last_image_cut = 0
                        for image_cut in header.get("images_length"):
                            images.append(data[last_image_cut:image_cut])
                            last_image_cut = image_cut
                        images.append(data[last_image_cut:])

                        result = self.handle(images) # results is a list of lines

                        self._log("Start sending back features at %f" % time.time())
                        data_back = json.dumps(result)
                        packet = struct.pack("!I%ds" % len(data_back), len(data_back), data_back)
                        self.master_sock.sendall(packet)
                        self._log(SLAVE_TAG + "Sent back features of frame %d at %f" % (frame_id, time.time()))
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
        # extract feature 
        result = extract_feature(images, self.txyc_sock, self.is_print)
        return result

class SlaveClassification(threading.Thread):
    def __init__(self, master_addr, is_print = True):
        self.stop = threading.Event()
        self.is_print = is_print
        try:
            self.master_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.master_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.master_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.master_sock.connect(master_addr)
        except socket.error as e:
            LOG.warning(SLAVE_TAG + "Failed to connect to %s" % str(master_addr))
        threading.Thread.__init__(self, target=self.run)

    def _log(self, message):
        if self.is_print:
            LOG.info(SLAVE_TAG + message)

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
        LOG.info(SLAVE_TAG + "Start getting data from master classification node")
        socket_fd = self.master_sock.fileno()
        input_list = [socket_fd]
        try:
            while(not self.stop.wait(0.001)):
                inputready, outputready, exceptready = \
                        select.select(input_list, [], [], 0)
                for s in inputready:
                    if s == socket_fd:
                        self._log("Start receiving new feature vecture at %f" % time.time())
                        data_size = struct.unpack("!I", self._recv_all(self.master_sock, 4))[0]
                        data_json = self._recv_all(self.master_sock, data_size)
                        model_idxes, feature_list = json.loads(data_json)
                        feature_vec = compute_hist(feature_list)
                        #feature_vec = list(feature_vec.items())
                        #feature_vec = dict(feature_vec)
                        self._log("Finished receiving feature vector at %f" % time.time())
                        
                        confidences = []
                        for model_idx in model_idxes:
                            confidences.append(compute_confidence(feature_vec, model_idx))

                        data_back = json.dumps(confidences)
                        self._log("Starting to send back confidence scores at %f" % time.time())
                        packet = struct.pack("!I%ds" % len(data_back), len(data_back), data_back)
                        self.master_sock.sendall(packet)
                        self._log("Sent back confidences at %f" % time.time())
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
    

def GET(url):
    meta_stream = urllib2.urlopen(url)
    meta_raw = meta_stream.read()
    ret = json.loads(meta_raw)
    return ret

def POST(url, json_string):
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

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 80)) 
    return s.getsockname()[0]

def register_as_master(url):
    IP = get_local_ip()
    json_info = {
        "master_address": "%s:%d" % (IP, MASTER_PORT),
    }
    POST(url, json_info)
    return IP, MASTER_PORT

if __name__ == "__main__":
    sys.stdout.write("Finding control VM\n")
    service_list = get_service_list(sys.argv)
    video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
    video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
    return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

    # master node discovery
    master_info_url = "http://%s:%d%s" % (video_ip, 8021, "/services/motion")
    try:
        master_info = GET(master_info_url)
        master_node_addr = master_info.get('service_content').get('master_address')
        master_node_ip, master_node_port = master_node_addr.strip().split(':')
        master_node_port = int(master_node_port)
        is_master = False
    except Exception as e:
        LOG.info(MASTER_TAG + "Current node serves as master node")
        master_node_ip, master_node_port = register_as_master(master_info_url)
        is_master = True

    LOG.info(SLAVE_TAG + "connected to %s:%d" % (master_node_ip, master_node_port))

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
        master_classification = MasterClassification(feature_queue, output_queue_list, 10)
        master_classification.start()
        master_classification.isDaemon = True
        result_pub = ResultpublishClient(return_addresses, output_queue_list) # this is where output_queues are created according to each return_address
        result_pub.start()
        result_pub.isDaemon = True

        LOG.info(MASTER_TAG + "Start receiving data")
    else: # Slave proxy
        txyc_thread = AppLauncher(TXYC_PATH, args = TXYC_ARGS, is_print=True)
        txyc_thread.start()
        txyc_thread.isDaemon = True
        time.sleep(3)
        slave_proxy = SlaveProxy((master_node_ip, master_node_port), ("localhost", TXYC_PORT), is_print = True)
        slave_proxy.start()
        slave_proxy.isDaemon = True
        slave_classification = SlaveClassification((master_node_ip, MASTER_CLASSIFICATION_PORT), is_print = not is_master)
        slave_classification.start()
        slave_classification.isDaemon = True

        LOG.info(SLAVE_TAG + "Start receiving data")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
    except Exception as e:
        pass
    finally:
        if is_master:
            if master_streaming:
                master_streaming.terminate()
            if master_proxy:
                master_proxy.terminate()
            if master_classification:
                master_classification.terminate()
            if result_pub:
                result_pub.terminate()
        else:
            if txyc_thread:
                txyc_thread.terminate()
            if slave_proxy:
                slave_proxy.terminate()
            if slave_classification:
                slave_classification.terminate()
