# edited by Norbert (mjpeg part) from a file from Copyright Jon Berg , turtlemeat.com,
# MJPEG Server for the webcam


import cgi
import time
import sys

from os import curdir, sep
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
from SocketServer import ThreadingMixIn
import SocketServer
import threading
import functools
import select
import traceback
import Queue
import struct
import re
import socket
import json
from cloudlet import log as logging


LOG = logging.getLogger(__name__)
image_queue_list = list()
result_queue_list = list()


class Conf(object):
    MOBILE_PORT = 9098
    HTTP_PORT = 8080
    VIDEO_PORT = 10101

    MAX_FRAME_SIZE = 10

class Protocol_client(object):
    CONTROL_MESSAGE_KEY = "control"
    RESULT_MESSAGE_KEY = "result"


class StreamingServerError(Exception):
    pass


def wrap_process_fault(function):
    """Wraps a method to catch exceptions related to instances.
    This decorator wraps a method to catch any exceptions and
    terminate the request gracefully.
    """
    @functools.wraps(function)
    def decorated_function(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception as e:
            if hasattr(self, 'exception_handler'):
                self.exception_handler()
            kwargs.update(dict(zip(function.func_code.co_varnames[2:], args)))
            LOG.error("failed with : %s" % str(kwargs))

    return decorated_function


class MobileVideoHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(MobileVideoHandler, self).setup()
        self.average_FPS = 0.0
        self.current_FPS = 0.0
        self.init_connect_time = None
        self.previous_time = None
        self.current_time = 0
        self.frame_count = 0

    def _handle_image_input(self):
        img_size = self.request.recv(4)
        if img_size is None or len(img_size) != 4:
            msg = "Failed to receive first byte of header"
            raise StreamingServerError(msg)
        img_size = struct.unpack("!I", img_size)[0]
        image_data = self.request.recv(img_size)
        while len(image_data) < img_size:
            image_data += self.request.recv(img_size - len(image_data))
        self.frame_count += 1

        # measurement
        self.current_time = time.time()
        self.current_FPS = 1 / (self.current_time - self.previous_time)
        self.average_FPS = self.frame_count / (self.current_time - self.init_connect_time)
        self.previous_time = self.current_time

        if (self.frame_count%10 == 0):
            msg = "Video frame rate from client : current(%f), average(%f)" % \
                    (self.current_FPS, self.average_FPS)
            #LOG.info(msg)
        for image_queue in image_queue_list:
            if image_queue.full() is True:
                image_queue.get()
            image_queue.put(image_data)

    def _handle_result_output(self):
        global result_queue_list

        for result_queue in result_queue_list:
            result_msg = None
            try:
                result_msg = result_queue.get_nowait()
            except Queue.Empty:
                pass
            if result_msg is not None:
                ret_size = struct.pack("!I", len(result_msg))
                self.request.send(ret_size)
                self.wfile.write(result_msg)
                self.wfile.flush()
                LOG.info("result message (%s) sent to the Glass", result_msg)

    def handle(self):
        global image_queue_list
        try:
            LOG.info("new Google Glass is connected")
            self.init_connect_time = time.time()
            self.previous_time = time.time()

            socket_fd = self.request.fileno()
            input_list = [socket_fd]
            output_list = [socket_fd]
            while True:
                inputready, outputready, exceptready = \
                        select.select(input_list, output_list, [])
                for s in inputready:
                    if s == socket_fd:
                        self._handle_image_input()
                for output in outputready:
                    if output == socket_fd:
                        self._handle_result_output()

        except Exception as e:
            LOG.info(traceback.format_exc())
            LOG.info("%s" % str(e))
            LOG.info("handler raises exception\n")
            LOG.info("Client disconnected")
            self.terminate()

    def terminate(self):
        pass


class VideoStreamingHandler(SocketServer.StreamRequestHandler, object):
    def setup(self):
        super(MobileVideoHandler, self).setup()
        global image_queue_list

        self.data_queue = Queue.Queue(Conf.MAX_FRAME_SIZE)
        self.result_queue = Queue.Queue()
        image_queue_list.append(self.data_queue)
        result_queue_list.append(self.result_queue)

    def handle(self):
        try:
            LOG.info("new AppVM is connected")
            while True:
                if self.data_queue.empty() == False:
                    image_data = self.data_queue.get()
                    LOG.info("getting new image")
                    image_size = struct.pack("!I", len(image_data))
                    self.request.send(image_size)
                    self.wfile.write(image_data)
                    self.wfile.flush()
                    time.sleep(0.0001)
        except Exception as e:
            sys.stderr.write(traceback.format_exc())
            sys.stderr.write("%s" % str(e))
            sys.stderr.write("handler raises exception\n")
            self.terminate()


    def terminate(self):
        global image_queue_list
        global result_queue_list

        image_queue_list.remove(self.data_queue)
        result_queue_list.remove(self.result_queue)
        pass


class MJPEGStreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global cameraQuality
        try:
            self.path = re.sub('[^.a-zA-Z0-9]', "", str(self.path))
            if self.path== "" or self.path == None or self.path[:1] == ".":
                return
            if self.path.endswith(".html"):
                f = open(curdir + sep + self.path)
                self.send_response(200)
                self.send_header('Content-type',	'text/html')
                self.end_headers()
                self.wfile.write(f.read())
                f.close()
                return
            if self.path.endswith(".mjpeg"):
                self.send_response(200)
                self.wfile.write("Content-Type: multipart/x-mixed-replace; boundary=--aaboundary")
                self.wfile.write("\r\n\r\n")
                while 1:
                    #img = cv.QueryFrame(capture)
                    #cv2mat=cv.EncodeImage(".jpeg",img,(cv.CV_IMWRITE_JPEG_QUALITY,cameraQuality))
                    #JpegData=cv2mat.tostring()
                    if image_queue.empty() == False:
                        image_data = image_queue.get()
                        LOG.info("getting new image")
                        self.wfile.write("--aaboundary\r\n")
                        self.wfile.write("Content-Type: image/jpeg\r\n")
                        self.wfile.write("Content-length: " + str(len(image_data)) + "\r\n\r\n")
                        self.wfile.write(image_data)
                        self.wfile.write("\r\n\r\n\r\n")
                        time.sleep(0.01)
                        image_queue.task_done()
                return
            if self.path.endswith(".jpeg"):
                f = open(curdir + sep + self.path)
                self.send_response(200)
                self.send_header('Content-type', 'image/jpeg')
                self.end_headers()
                self.wfile.write(f.read())
                f.close()
                return
            return
        except IOError:
            self.send_error(404,'File Not Found: %s' % self.path)
    def do_POST(self):
        global rootnode, cameraQuality
        try:
            ctype, pdict = cgi.parse_header(self.headers.getheader('content-type'))
            if ctype == 'multipart/form-data':
                query=cgi.parse_multipart(self.rfile, pdict)
            self.send_response(301)

            self.end_headers()
            upfilecontent = query.get('upfile')
            print "filecontent", upfilecontent[0]
            value = int(upfilecontent[0])
            cameraQuality = max(2, min(99, value))
            self.wfile.write("<HTML>POST OK. Camera Set to<BR><BR>")
            self.wfile.write(str(cameraQuality))

        except :
            pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
#class ThreadedHTTPServer(HTTPServer):
    """Handle requests in a separate thread."""



def get_local_ipaddress():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("gmail.com", 80))
    ipaddress = (s.getsockname()[0])
    s.close()
    return ipaddress


def main():
    mobile_server = ThreadedHTTPServer(('0.0.0.0', Conf.MOBILE_PORT), MobileVideoHandler)
    mobile_server.allow_reuse_address = True
    mobile_server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    mobile_server.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    video_server = ThreadedHTTPServer(('0.0.0.0', Conf.VIDEO_PORT), VideoStreamingHandler)
    video_server.allow_reuse_address = True
    video_server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    video_server.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    LOG.info('started mobile server at %s' % (str(Conf.MOBILE_PORT)))
    mobile_server_thread = threading.Thread(target=mobile_server.serve_forever)
    mobile_server_thread.daemon = True
    LOG.info('started AppVM server at %s' % (str(Conf.VIDEO_PORT)))
    video_server_thread = threading.Thread(target=video_server.serve_forever)
    video_server_thread.daemon = True

    try:
        mobile_server_thread.start()
        video_server_thread.start()

        global result_queue_list
        emulated_result_queue = Queue.Queue()
        result_queue_list.append(emulated_result_queue)
        while True:
            user_input = raw_input("Enter result: ")
            if user_input.lower() == 'q':
                break
            else:
                json_result = json.dumps({
                    Protocol_client.RESULT_MESSAGE_KEY:str(user_input),
                    })
                emulated_result_queue.put(json_result)
    except Exception as e:
        #sys.stderr.write(str(e))
        #server.terminate()
        sys.exit(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        #server.terminate()
        sys.exit(1)
    else:
        #server.terminate()
        sys.exit(0)



if __name__ == '__main__':
    main()
