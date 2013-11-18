#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Kiryong Ha <krha@cmu.edu>
#   Modify: Wenlu Hu <wenlu@cmu.edu>
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
import time
import Queue

import socket
import sys
import time
import os 

from app_proxy import AppProxyError
from app_proxy import AppProxyStreamingClient
from app_proxy import AppProxyThread
from app_proxy import ResultpublishClient
from app_proxy import LOG
from app_proxy import get_service_list
from app_proxy import SERVICE_META


class FaceThread(AppProxyThread):
    def handle(self, header, data):
        
	self.imagecount += 1
	# fn = 'testimage' + str(self.imagecount) + '.jpg'
	fn = 'image2OCR.jpg'
	f = open(fn, 'wb')
        f.write(data)
	f.close()
	#self.count += 1

        # call VeryPDF to OCR the image file
        os.system("powershell ocr_1image.ps1 " + fn)

        # Read the output text from txt file
	outfn = fn + '.txt'
        f = open(outfn, 'r')
	result = f.read()
	f.close()

        result = result.replace('\n', ' ')
        import re
	ret = re.sub(r'[^A-Za-z0-9 ]', '', result)

        # ret = result.replace('\n','')
	# ret = ret.replace('\r','')
        # ret = ret[:-1]
	
	print str(self.imagecount) + ":\t" + ret
        return ret

    def __init__(self, image_queue, output_queue):
        super(FaceThread, self).__init__(image_queue, output_queue)
	self.imagecount = 0


	'''
    def terminate(self):
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

        # feed data to the app
        (found_name, position) = face_request(self.app_sock, data)
        if found_name.find("\\u0000") != -1:
            import pdb;pdb.set_trace()

        if len(found_name.strip()) != 0:
            return found_name
        return None
	'''

if __name__ == "__main__":
    image_queue = Queue.Queue(1)
    output_queue_list = list()

    face_thread = None
    client_thread = None
    result_pub = None

    try:
        sys.stdout.write("Finding control VM\n")
        service_list = get_service_list(sys.argv)
        video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
        video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
	return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

        client_thread = AppProxyStreamingClient((video_ip, video_port), image_queue)
        client_thread.start()
        client_thread.isDaemon = True
        face_thread = FaceThread(image_queue, output_queue_list)
        face_thread.start()
        face_thread.isDaemon = True 
	
	# result pub/sub
	result_pub = ResultpublishClient(return_addresses, output_queue_list)
	result_pub.start()
	result_pub.isDaemon = True

        LOG.info("Start receiving data\n")
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
    except Exception as e:
	import traceback
	LOG.warning(traceback.format_exc())
	LOG.warning("%s" % str(e))
        LOG.warning(str(e))
    finally:
        if client_thread != None:
            client_thread.terminate()
        if app_thread != None:
            app_thread.terminate()
        if face_thread != None:
            face_thread.terminate()
	if result_pub != None:
	    result_pub.terminate()

