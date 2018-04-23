#! /usr/bin/env python
import json
import multiprocessing
import numpy as np
from optparse import OptionParser
import os
import Queue
import struct
import sys
import threading
import time

dir_file = os.path.dirname(os.path.realpath(__file__))

sys.path.insert(0, os.path.join(dir_file, "../../server"))
import gabriel
LOG = gabriel.logging.getLogger(__name__)

n_correct = n_wrong = 0
has_update = False

def process_command_line(argv):
    parser = OptionParser()
    parser.add_option(
            '-i', '--input', action = 'store', dest = 'image_dir',
            help = "directory for input images")
    parser.add_option(
            '-r', '--frame_rate', type = "int", action = 'store', dest = 'frame_rate', default = 15,
            help = "the frame rate for loading jpeg images")
    parser.add_option(
            '-s', '--server_IP', action = 'store',
            help = "IP address of Gabriel server")
    parser.add_option(
            '-t', "--truth_file", action = 'store',
            help = "File containing ground truth")

    settings, args = parser.parse_args(argv)

    if not hasattr(settings, 'image_dir') or settings.image_dir is None:
        parser.error("You have to provide an input directory")
    if not os.path.isdir(settings.image_dir):
        parser.error("%s is not a directory" % settings.image_dir)
    return settings

class Truth:
    def __init__(self, ground_truth):
        self.truth = ground_truth
        self.img_idx = 0
        self.list_idx = 0

    def get_next(self):
        self.img_idx += 1
        if self.img_idx > self.truth[self.list_idx][1]:
            self.list_idx += 1
        return self.truth[self.list_idx][2]

    def get(self, frame_id):
        for item in self.truth:
            if frame_id <= item[1]:
                return item[2]
        return "unknown"

#################  minimal CV functionality  ################################
import cv2
def display_image(display_name, img, wait_time = 1, resize_max = None):
    if resize_max is not None:
        img_shape = img.shape
        height = img_shape[0]; width = img_shape[1]
        if height > width:
            img_display = cv2.resize(img, (resize_max * width / height, resize_max), interpolation = cv2.INTER_NEAREST)
        else:
            img_display = cv2.resize(img, (resize_max, resize_max * height / width), interpolation = cv2.INTER_NEAREST)
    else:
        img_display = img

    cv2.imshow(display_name, img_display)
    cv2.waitKey(wait_time)

def display_state(name, T, n_correct, n_wrong, FPS):
    img_display = np.ones((200, 640, 3), dtype = np.uint8) * 100
    cv2.putText(img_display, "detected: %s, truth: %s" % (name, T), (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, [0, 255, 0], thickness = 2)
    cv2.putText(img_display, "correct: %d, wrong: %d" % (n_correct, n_wrong), (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, [0, 255, 0], thickness = 2)
    cv2.putText(img_display, "accuracy: %.2f, FPS: %.2f" % (float(n_correct) / (n_correct + n_wrong), FPS), (10, 160), cv2.FONT_HERSHEY_SIMPLEX, 1, [0, 255, 0], thickness = 2)

    display_image('state', img_display, resize_max = 640)

def raw2cv_image(raw_data):
    img_array = np.asarray(bytearray(raw_data), dtype = np.int8)
    cv_image = cv2.imdecode(img_array, -1)
    return cv_image
#############################################################################



class TokenController(object):
    def __init__(self, token_size):
        super(self.__class__, self).__init__()
        self.current_token = token_size
        self.token_lock = threading.Lock()
        self.token_lock_condition = threading.Condition(self.token_lock)
        self.sent_packets = {}
        self.prev_recv_frame_ID = 0

    def log_sent_packet(self, frame_ID, data_time):
        self.sent_packets[frame_ID] = [data_time]

    def get_current_token(self):
        return self.current_token

    def increase_tokens(self, count):
        with self.token_lock:
            self.current_token += count

    def decrease_token(self):
        with self.token_lock:
            if self.current_token > 0:
                self.current_token -= 1

    def refill_tokens(self, recv_frame_ID, time_received):
        # increase appropriate amount of tokens
        increase_count = 0
        for frame_ID in xrange(self.prev_recv_frame_ID + 1, recv_frame_ID):
            if frame_ID in self.sent_packets:
                increase_count += 1
                del self.sent_packets[frame_ID]
        self.increase_tokens(increase_count)

        # deal with the current response
        current_frame_info = self.sent_packets.get(recv_frame_ID, None)
        if current_frame_info is not None:
            # do not increase token if have already received duplicated ack
            if recv_frame_ID > self.prev_recv_frame_ID:
                self.increase_tokens(1)
                self.prev_recv_frame_ID = recv_frame_ID


class ControlThread(gabriel.network.CommonClient):
    """
    Exchange control messages with the server
    """
    def __init__(self, server_addr, cmd_queue):
        gabriel.network.CommonClient.__init__(self, server_addr)
        self.data_queue = cmd_queue

    def __repr__(self):
        return "Control Thread"

    def _handle_queue_data(self):
        try:
            cmd = self.data_queue.get(timeout = 0.0001)
        except Queue.Empty as e:
            Log.warning("Command line empty")

    def terminate(self):
        LOG.info("ControlThread terminating")
        super(ControlThread, self).terminate()


class VideoStreamingThread(gabriel.network.CommonClient):
    """
    This thread streams "captured" video frame to the server when token is available.
    """
    def __init__(self, server_addr, image_queue, token_controller):
        gabriel.network.CommonClient.__init__(self, server_addr)
        self.data_queue = image_queue
        self.token_controller = token_controller

    def __repr__(self):
        return "Video Streaming Thread"

    def _handle_queue_data(self):
        if self.token_controller.get_current_token() <= 0:
            LOG.debug("no token available")
            return

        try:
            (header_data, image_data) = self.data_queue.get(timeout = 0.0001)

            global has_update
            if has_update:
                cv_image = raw2cv_image(image_data)
                display_image("send", cv_image, resize_max = 640)
                has_update = False

            header_json = json.loads(header_data)
            data_time = time.time()
            frame_id = header_json[gabriel.Protocol_client.JSON_KEY_FRAME_ID]

            packet = struct.pack("!I%ds" % len(header_data), len(header_data), header_data)
            self.sock.sendall(packet)
            packet = struct.pack("!I%ds" % len(image_data), len(image_data), image_data)
            self.sock.sendall(packet)
            LOG.info("sending an image frame to Gabriel server")

            self.token_controller.log_sent_packet(frame_id, data_time)
            self.token_controller.decrease_token()
        except Queue.Empty as e:
            pass

    def terminate(self):
        LOG.info("VideoStreamingThread terminating")
        super(VideoStreamingThread, self).terminate()


class ResultReceivingThread(gabriel.network.CommonClient):
    """
    This client will receive data from the Gabriel server, and refill the token.
    """
    def __init__(self, server_addr, token_controller):
        gabriel.network.CommonClient.__init__(self, server_addr)
        self.token_controller = token_controller

        self.n_frames = 0
        self.start_time = time.time()

    def __repr__(self):
        return "Result Receiving Thread"

    def _handle_input_data(self):
        # receive data from control VM
        data_size = struct.unpack("!I", self._recv_all(4))[0]
        data_str = self._recv_all(data_size)
        time_received = time.time()

        # get some information from the returned result
        data_json = json.loads(data_str)
        frame_id = data_json[gabriel.Protocol_client.JSON_KEY_FRAME_ID]
        result = data_json.get(gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE, None)
        if result is not None:
            result_json = json.loads(result)

            # get speech guidance
            speech_feedback = result_json.get("speech", None)

            # get state, if any
            state = result_json.get("state", None)
            is_trust = result_json.get("trust", None)
            global ground_truth
            T = ground_truth.get((frame_id - 1) % 1081 + 1)
            print T

            if is_trust:
                global n_correct, n_wrong
                global has_update
                has_update = True
                if T == state:
                    n_correct += 1
                else:
                    n_wrong += 1
                self.n_frames += 1
                FPS = self.n_frames / (time.time() - self.start_time)
                display_state(state, T, n_correct, n_wrong, FPS)

        self.token_controller.refill_tokens(frame_id, time_received)

    def terminate(self):
        LOG.info("ResultReceivingThread terminating")
        super(ResultReceivingThread, self).terminate()

class ImageFeedingThread(threading.Thread):
    """
    This thread loads images from a folder, and put them into a queue.
    """
    def __init__(self, image_dir, image_queue, frame_rate = 15):
        # load images
        self.file_list = [os.path.join(image_dir, f) for f in os.listdir(image_dir)
                if f.lower().endswith("jpeg") or f.lower().endswith("jpg") or f.lower().endswith("bmp")]
        self.file_list.sort()

        self.image_queue = image_queue
        self.wait_time = 1.0 / frame_rate
        self.stop = threading.Event()
        self.frame_count = 1
        threading.Thread.__init__(self, target = self.run)


    def run(self):
        image_idx = 0
        while True:
            if self.stop.is_set():
                break

            image_idx = (image_idx + 1) % len(self.file_list)

            image_file = self.file_list[image_idx]
            image_data = open(image_file, "r").read()
            #cv_image = raw2cv_image(image_data)
            #display_image("client", cv_image, resize_max = 640)

            header_data = json.dumps({"type" : "python", gabriel.Protocol_client.JSON_KEY_FRAME_ID : self.frame_count})
            if self.image_queue.full():
                try:
                    image_queue.get_nowait()
                except Queue.Empty as e:
                    pass
            self.image_queue.put((header_data, image_data))

            if self.frame_count % 100 == 0:
                pass
                #LOG.info("pushing emualted image to the queue (%d)" % self.frame_count)
            self.frame_count += 1
            time.sleep(self.wait_time)

    def terminate(self):
        LOG.info("ImageFeedingThread terminating")
        self.stop.set()

if __name__ == '__main__':
    settings= process_command_line(sys.argv[1:])

    module_path, module_name = settings.truth_file.rsplit('/', 1)
    sys.path.insert(0, module_path)
    truth_module = __import__(module_name)
    global ground_truth
    ground_truth = Truth(truth_module.ground_truth)

    # token controller
    token_controller = TokenController(gabriel.Const.MAX_TOKEN_SIZE)
    LOG.info("TOKEN SIZE: %d" % gabriel.Const.MAX_TOKEN_SIZE)

    # control thread
    cmd_queue = multiprocessing.Queue(10)
    control_thread = ControlThread((settings.server_IP, gabriel.Const.MOBILE_SERVER_CONTROL_PORT), cmd_queue)
    control_thread.start()
    control_thread.isDaemon = True

    # result receiving thread
    result_receiving_thread = ResultReceivingThread((settings.server_IP, gabriel.Const.MOBILE_SERVER_RESULT_PORT), token_controller)
    result_receiving_thread.start()
    result_receiving_thread.isDaemon = True

    # video streaming thread
    image_queue = multiprocessing.Queue(gabriel.Const.MAX_TOKEN_SIZE)
    video_streaming_thread = VideoStreamingThread((settings.server_IP, gabriel.Const.MOBILE_SERVER_VIDEO_PORT), image_queue, token_controller)
    video_streaming_thread.start()
    video_streaming_thread.isDaemon = True

    # the thread to load image to put into image queue
    image_feeding_thread = ImageFeedingThread(settings.image_dir, image_queue, frame_rate = settings.frame_rate)
    image_feeding_thread.start()
    image_feeding_thread.isDaemon = True

    try:
        while True:
            time.sleep(1)
    except Exception as e:
        pass
    except KeyboardInterrupt as e:
        LOG.info("user exits\n")
    finally:
        if video_streaming_thread is not None:
            video_streaming_thread.terminate()
        if result_receiving_thread is not None:
            result_receiving_thread.terminate()
        if control_thread is not None:
            control_thread.terminate()
        if image_feeding_thread is not None:
            image_feeding_thread.terminate()
