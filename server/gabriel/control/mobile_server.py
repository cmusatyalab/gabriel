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
import multiprocessing
import os
import select
import socket
import struct
import sys
import threading
import time
import traceback
import base64
import gabriel

if (sys.version_info > (3, 0)):
    import queue as Queue
else:
    import Queue

LOG = gabriel.logging.getLogger(__name__)

image_queue_list = list()
acc_queue_list = list()
audio_queue_list = list()
gps_queue_list = list()
# a global queue that contains final messages sent back to the client
result_queue = multiprocessing.Queue()
# a global queue that contains control messages to be sent to the client
command_queue = multiprocessing.Queue()

# a global queue used to publish input streams in a web server for debugging purposes
input_display_queue = multiprocessing.Queue(1)
# a global queue used to publish output streams in a web server for debugging purposes
output_display_queue_dict = {'image': multiprocessing.Queue(1),
                             'debug': multiprocessing.Queue(1),
                             'text': multiprocessing.Queue(3),
                             'video': multiprocessing.Queue(3)}


class MobileCommError(Exception):
    pass


class MobileSensorHandler(gabriel.network.CommonHandler):
    def setup(self):
        super(MobileSensorHandler, self).setup()
        if gabriel.Debug.LOG_STAT:
            self.init_connect_time = None
            self.previous_time = None

    def handle(self):
        LOG.info("Mobile client is connected for (%s)" % str(self))
        if gabriel.Debug.LOG_STAT:
            self.init_connect_time = time.time()
            self.previous_time = time.time()
        super(MobileSensorHandler, self).handle()

        
class MobileControlHandler(MobileSensorHandler):
    '''
    The control server that
    1) Receive control messages from client (e.g. ping to synchronize time)
    2) Delivers sensor control messages from the applications
    '''
    def setup(self):
        super(MobileControlHandler, self).setup()

        # flush out old result at Queue
        while not command_queue.empty():
            command_queue.get()
        self.data_queue = command_queue

    def __repr__(self):
        return "Mobile Control Server"

    def _handle_input_data(self):
        ## receive data
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        header_json = json.loads(header_data.decode('utf-8'))

        # if "sync_time" field exists in the header, then this is a time sync request
        # and a local time is returned immediately
        if header_json.get("sync_time") is not None:
            header_json["sync_time"] = int(time.time() * 1000) # in millisecond
            header_data = json.dumps(header_json)
            packet = struct.pack("!I%ds" % len(header_data), len(header_data), header_data)
            self.request.send(packet)
            self.wfile.flush()
            return

    def _handle_queue_data(self):
        try:
            cmd_data = self.data_queue.get(timeout = 0.0001)
            cmd_json = json.loads(cmd_data.decode('utf-8'))

            if isinstance(cmd_json, list):
                for cmd_j in cmd_json:
                    cmd_data = json.dumps(cmd_j)
                    packet = struct.pack("!I%ds" % len(cmd_data), len(cmd_data), cmd_data)
                    self.request.send(packet)
                    self.wfile.flush()
                    LOG.info("command sent to mobile device: %s", cmd_data)

            else:
                ## send return data to the mobile device
                packet = struct.pack("!I%ds" % len(cmd_data), len(cmd_data), cmd_data)
                self.request.send(packet)
                self.wfile.flush()
                LOG.info("command sent to mobile device: %s", cmd_data)

        except Queue.Empty:
            LOG.warning("data queue shouldn't be empty! - %s" % str(self))



class MobileVideoHandler(MobileSensorHandler):
    '''
    The video stream server that
    1) takes MJPEG streams as input, and
    2) put the image data into queues that are then sent to different cognitive engines
    Optionally, it can also syncronize time with client
    '''
    def setup(self):
        super(MobileVideoHandler, self).setup()
        if gabriel.Debug.LOG_STAT:
            self.frame_count = 0
            self.total_recv_size = 0
        if gabriel.Debug.SAVE_IMAGES:
            if not os.path.exists(gabriel.Const.LOG_IMAGES_PATH):
                os.makedirs(gabriel.Const.LOG_IMAGES_PATH)
            self.log_images_counter = 0
            self.log_images_timing = open(os.path.join(gabriel.Const.LOG_IMAGES_PATH, "timing.txt"), "w")
        if gabriel.Debug.SAVE_VIDEO:
            self.log_video_writer_created = False

    def __repr__(self):
        return "Mobile Video Server"

    def _handle_input_data(self):
        ## receive data
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        image_size = struct.unpack("!I", self._recv_all(4))[0]
        image_data = self._recv_all(image_size)

        ## the gabriel test app that does nothing...
        if gabriel.Debug.DIRECT_RETURN:
            packet = struct.pack("!I%ds" % len(header_data),
                    len(header_data), header_data)
            self.request.send(packet)
            self.wfile.flush()

        ## add header data for measurement
        if gabriel.Debug.TIME_MEASUREMENT:
            header_json = json.loads(header_data.decode('utf-8'))
            header_json[gabriel.Protocol_measurement.JSON_KEY_CONTROL_RECV_FROM_MOBILE_TIME] = time.time()
            header_data = json.dumps(header_json)

        ## stats
        if gabriel.Debug.LOG_STAT:
            self.frame_count += 1
            current_time = time.time()
            self.total_recv_size += (header_size + image_size + 8)
            current_FPS = 1 / (current_time - self.previous_time)
            self.previous_time = current_time
            average_FPS = self.frame_count / (current_time - self.init_connect_time)

            if (self.frame_count % 100 == 0):
                log_msg = "Video FPS : current(%f), avg(%f), BW(%f Mbps), offloading engine(%d)" % \
                        (current_FPS, average_FPS, 8 * self.total_recv_size / (current_time - self.init_connect_time) / 1000 / 1000, len(image_queue_list))
                LOG.info(log_msg)

        ## put current image data in all registered cognitive engine queue
        for image_queue in image_queue_list:
            if image_queue.full():
                try:
                    image_queue.get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                image_queue.put_nowait((header_data, image_data))
            except Queue.Full as e:
                pass

        ## display input stream for debug purpose
        if gabriel.Debug.WEB_SERVER:
            if input_display_queue.full():
                try:
                    input_display_queue.get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                input_display_queue.put_nowait(image_data)
            except Queue.Full as e:
                pass

        ## write images into files
        if gabriel.Debug.SAVE_IMAGES:
            self.log_images_counter += 1
            with open(os.path.join(gabriel.Const.LOG_IMAGES_PATH, "frame-" + gabriel.util.add_preceding_zeros(self.log_images_counter) + ".jpeg"), "w") as f:
                f.write(image_data)
            if self.log_images_timing is not None:
                self.log_images_timing.write("%d,%d\n" % (self.log_images_counter, int(time.time() * 1000)))

        ## write images into a video
        if gabriel.Debug.SAVE_VIDEO:
            import cv2
            import numpy as np
            img_array = np.asarray(bytearray(image_data), dtype = np.int8)
            cv_image = cv2.imdecode(img_array, -1)
            print(cv_image.shape)
            if not self.log_video_writer_created:
                self.log_video_writer_created = True
                self.log_video_writer = cv2.VideoWriter(gabriel.Const.LOG_VIDEO_PATH, cv2.cv.CV_FOURCC('X','V','I','D'), 10, (cv_image.shape[1], cv_image.shape[0]))
            self.log_video_writer.write(cv_image)


class MobileAccHandler(MobileSensorHandler):
    def setup(self):
        super(MobileAccHandler, self).setup()
        if gabriel.Debug.LOG_STAT:
            self.frame_count = 0
            self.total_recv_size = 0
        if gabriel.Debug.SAVE_ACC:
            self.acc_log = open(gabriel.Const.LOG_ACC_PATH, "w")

    def __repr__(self):
        return "Mobile Acc Server"

    def chunks(self, data, n):
        for i in xrange(0, len(data), n):
            yield data[i : i + n]

    def _handle_input_data(self):
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        acc_size = struct.unpack("!I", self._recv_all(4))[0]
        acc_data = self._recv_all(acc_size)

        ## stats
        if gabriel.Debug.LOG_STAT:
            self.frame_count += 1
            current_time = time.time()
            self.total_recv_size += (header_size + acc_size + 8)
            current_FPS = 1 / (current_time - self.previous_time)
            self.previous_time = current_time
            average_FPS = self.frame_count / (current_time - self.init_connect_time)

            if (self.frame_count % 100 == 0):
                log_msg = "ACC FPS : current(%f), avg(%f), BW(%f Mbps), offloading engine(%d)" % \
                        (current_FPS, average_FPS, 8 * self.total_recv_size / (current_time - self.init_connect_time) / 1000 / 1000, len(acc_queue_list))
                LOG.info(log_msg)

        ## log acc data
        if gabriel.Debug.SAVE_ACC:
            ACC_SEGMENT_SIZE = 12 # (float, float, float)
            t = int(time.time() * 1000)
            for chunk in self.chunks(acc_data, ACC_SEGMENT_SIZE):
                (acc_x, acc_y, acc_z) = struct.unpack("!fff", chunk)
                self.acc_log.write("%d,%f,%f,%f\n" % (t, acc_x, acc_y, acc_z))

        ## put current acc data in all registered cognitive engine queue
        for acc_queue in acc_queue_list:
            if acc_queue.full():
                try:
                    acc_queue.get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                acc_queue.put_nowait((header_data, acc_data))
            except Queue.Full as e:
                pass


class MobileAudioHandler(MobileSensorHandler):
    def setup(self):
        super(MobileAudioHandler, self).setup()
        if gabriel.Debug.LOG_STAT:
            self.frame_count = 0
            self.total_recv_size = 0

    def __repr__(self):
        return "Mobile Audio Server"

    def _handle_input_data(self):
        ## receive data
        header_size = struct.unpack("!I", self._recv_all(4))[0]
        header_data = self._recv_all(header_size)
        header_json = json.loads(header_data.decode('utf-8'))
        audio_size = struct.unpack("!I", self._recv_all(4))[0]
        audio_data = self._recv_all(audio_size)

        ## stats
        if gabriel.Debug.LOG_STAT:
            self.frame_count += 1
            current_time = time.time()
            self.total_recv_size += (header_size + audio_size + 8)
            current_FPS = 1 / (current_time - self.previous_time)
            self.previous_time = current_time
            average_FPS = self.frame_count / (current_time - self.init_connect_time)

            if (self.frame_count % 100 == 0):
                log_msg = "Audio FPS : current(%f), avg(%f), BW(%f Mbps), offloading engine(%d)" % \
                        (current_FPS, average_FPS, 8 * self.total_recv_size / (current_time - self.init_connect_time) / 1000 / 1000, len(audio_queue_list))
                LOG.info(log_msg)

        ## put current audio data in all registered cognitive engine queue
        for audio_queue in audio_queue_list:
            if audio_queue.full():
                try:
                    audio_queue.get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                audio_queue.put((header_data, audio_data))
            except Queue.Full as e:
                pass


class MobileResultHandler(MobileSensorHandler):
    def setup(self):
        super(MobileResultHandler, self).setup()

        # flush out old result at Queue
        while not result_queue.empty():
            result_queue.get()
        self.data_queue = result_queue

        if gabriel.Debug.TIME_MEASUREMENT:
            self.time_breakdown_log = open("log-time-breakdown.txt", "w")

    def __repr__(self):
        return "Mobile Result Server"

    @staticmethod
    def _add_data_to_debug_server(rtn_header_json, rtn_data_json):
        """Add data to debug server.
        Debug server accepts 4 types of data: annotated input image with detected object, video instruction,
        image instruction, and text instruction. It only supports instructions in json format (legacy).
        """
        # only the annotated debug image are in the header, everything else are in the data
        image_encoded = rtn_header_json.get(gabriel.Protocol_debug.JSON_KEY_ANNOTATED_INPUT_IMAGE, None)
        if image_encoded is not None:
            image_data = base64.b64decode(image_encoded)
            if output_display_queue_dict['debug'].full():
                try:
                    output_display_queue_dict['debug'].get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                output_display_queue_dict['debug'].put_nowait(image_data)
            except Queue.Full as e:
                pass

        # image response
        image_encoded = rtn_data_json.get('image', None)
        if image_encoded is not None:
            image_data = base64.b64decode(image_encoded)
            if output_display_queue_dict['image'].full():
                try:
                    output_display_queue_dict['image'].get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                output_display_queue_dict['image'].put_nowait(image_data)
            except Queue.Full as e:
                pass

        # text response
        text_data = rtn_data_json.get('speech', None)
        if text_data is not None:
            if output_display_queue_dict['text'].full():
                try:
                    output_display_queue_dict['text'].get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                output_display_queue_dict['text'].put_nowait(text_data)
            except Queue.Full as e:
                pass

        # video response
        video_url = rtn_data_json.get('video', None)
        if video_url is not None:
            if output_display_queue_dict['video'].full():
                try:
                    output_display_queue_dict['video'].get_nowait()
                except Queue.Empty as e:
                    pass
            try:
                output_display_queue_dict['video'].put_nowait(video_url)
            except Queue.Full as e:
                pass

    @staticmethod
    def _remove_debug_header_fields(rtn_header_json):
        rtn_header_json.pop(gabriel.Protocol_debug.JSON_KEY_ANNOTATED_INPUT_IMAGE, None)

    def _handle_queue_data(self):
        try:
            (rtn_header, rtn_data) = self.data_queue.get(timeout = 0.0001)
            rtn_header_json = json.loads(rtn_header.decode('utf-8'))
            ## log measured time
            if gabriel.Debug.TIME_MEASUREMENT:
                frame_id = rtn_header_json[gabriel.Protocol_client.JSON_KEY_FRAME_ID]
                now = time.time()

                control_recv_from_mobile_time = rtn_header_json.get(gabriel.Protocol_measurement.JSON_KEY_CONTROL_RECV_FROM_MOBILE_TIME, -1)
                app_recv_time = rtn_header_json.get(gabriel.Protocol_measurement.JSON_KEY_APP_RECV_TIME, -1)
                app_sent_time = rtn_header_json.get(gabriel.Protocol_measurement.JSON_KEY_APP_SENT_TIME, -1)
                symbolic_done_time = rtn_header_json.get(gabriel.Protocol_measurement.JSON_KEY_APP_SYMBOLIC_TIME, -1)
                ucomm_recv_time = rtn_header_json.get(gabriel.Protocol_measurement.JSON_KEY_UCOMM_RECV_TIME, -1)
                ucomm_sent_time = rtn_header_json.get(gabriel.Protocol_measurement.JSON_KEY_UCOMM_SENT_TIME, -1)

                # no need to send the time info back to the client
                rtn_header_json.pop(gabriel.Protocol_measurement.JSON_KEY_CONTROL_RECV_FROM_MOBILE_TIME, None)
                rtn_header_json.pop(gabriel.Protocol_measurement.JSON_KEY_APP_SENT_TIME, None)
                rtn_header_json.pop(gabriel.Protocol_measurement.JSON_KEY_APP_RECV_TIME, None)
                rtn_header_json.pop(gabriel.Protocol_measurement.JSON_KEY_UCOMM_RECV_TIME, None)
                rtn_header_json.pop(gabriel.Protocol_measurement.JSON_KEY_UCOMM_SENT_TIME, None)
                rtn_header_json.pop(gabriel.Protocol_measurement.JSON_KEY_APP_SYMBOLIC_TIME, None)

                if self.time_breakdown_log is not None:
                    self.time_breakdown_log.write("%s\t%f\t%f\t%f\t%f\t%f\t%f\t%f\n" %
                            (frame_id, control_recv_from_mobile_time, app_recv_time, symbolic_done_time, app_sent_time, ucomm_recv_time, ucomm_sent_time, now))

            if gabriel.Debug.WEB_SERVER:
                if gabriel.Const.LEGACY_JSON_ONLY_RESULT:
                    rtn_data_json = json.loads(rtn_data.decode('utf-8'))
                    self._add_data_to_debug_server(rtn_header_json, rtn_data_json)
                else:
                    raise NotImplementedError("Debug server only support legacy mode!")

            self._remove_debug_header_fields(rtn_header_json)

            ## send return data to the mobile device
            # packet format: header size, header, data
            # add data size as a field in header for backward compatibility
            rtn_header_json[gabriel.Protocol_client.JSON_KEY_DATA_SIZE]=len(rtn_data)
            rtn_header = json.dumps(rtn_header_json)

            if gabriel.Const.LEGACY_JSON_ONLY_RESULT:
                rtn_header_json[gabriel.Protocol_client.JSON_KEY_RESULT_MESSAGE]=rtn_data
                rtn_header=json.dumps(rtn_header_json)
                packet = struct.pack("!I{}s".format(len(rtn_header)), len(rtn_header), rtn_header)
                LOG.info("message sent to the Glass: %s", gabriel.util.print_rtn(rtn_header_json))
            else:
                packet = struct.pack("!I{}s{}s".format(len(rtn_header),len(rtn_data)), len(rtn_header), bytes(rtn_header, 'utf-8'), rtn_data)
                LOG.info("message sent to the Glass: %s", gabriel.util.print_rtn(rtn_header_json))
            self.request.send(packet)
            self.wfile.flush()


        except Queue.Empty:
            LOG.warning("data queue shouldn't be empty! - %s" % str(self))


class MobileCommServer(gabriel.network.CommonServer):
    def __init__(self, port, handler):
        gabriel.network.CommonServer.__init__(self, port, handler) # cannot use super because it's old style class
        LOG.info("* Mobile server(%s) configuration" % str(self.handler))
        LOG.info(" - Open TCP Server at %s" % (str(self.server_address)))
        LOG.info(" - Disable nagle (No TCP delay)  : %s" %
                str(self.socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)))
        LOG.info("-" * 50)

    def terminate(self):
        gabriel.network.CommonServer.terminate(self)


def main():
    video_server = MobileCommServer(gabriel.Const.MOBILE_SERVER_VIDEO_PORT, MobileVideoHandler)
    video_thread = threading.Thread(target=video_server.serve_forever)
    video_thread.daemon = True

    acc_server = MobileCommServer(gabriel.Const.MOBILE_SERVER_ACC_PORT, MobileAccHandler)
    acc_thread = threading.Thread(target=acc_server.serve_forever)
    acc_thread.daemon = True

    audio_server = MobileCommServer(gabriel.Const.MOBILE_SERVER_AUDIO_PORT, MobileAudioHandler)
    audio_thread = threading.Thread(target=audio_server.serve_forever)
    audio_thread.daemon = True

    try:
        video_thread.start()
        acc_thread.start()
        audio_thread.start()
        while True:
            time.sleep(100)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        video_server.terminate()
        acc_server.terminate()
        audio_server.terminate()
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(str(e))
        video_server.terminate()
        acc_server.terminate()
        audio_server.terminate()
        sys.exit(1)
    else:
        video_server.terminate()
        acc_server.terminate()
        audio_server.terminate()
        sys.exit(0)


if __name__ == '__main__':
    main()
