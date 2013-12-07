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
sys.path.insert(0, "../common")

from app_proxy import AppProxyError
from app_proxy import AppProxyStreamingClient
from app_proxy import AppProxyThread
from app_proxy import ResultpublishClient
from app_proxy import LOG
from app_proxy import get_service_list
from app_proxy import SERVICE_META
from energy import EnergyRecordingThread

class stfThread(AppProxyThread):
    def handle(self, header, data):
        
        if self.firstFrame:
            #TODO start energy recording
            energy_thread.start()
            self.firstFrame = False

        # Write data to file
        self.imagecount += 1
#       app_dir = "/usr0/home/wenluh/Development/Indexer/pstf/src/"
        # fn = 'testimage' + str(self.imagecount) + '.jpg'
        DEBUG=False
        imageSuff = ".bmp" #TODO use ".jpg" for real data from glass
        if DEBUG:
            fn = 'testpic' + str(self.imagecount) + imageSuff
        else:
            fn = 'testpic' + '0'  + imageSuff
        f = open(fn, 'wb')
        f.write(data)
        f.close()

        # runSTF.py: run STF on the image file 
        import subprocess
        output = subprocess.check_output(["bash", "./runSTF.sh", fn])
#       output = subprocess.check_output(["echo", fn])
        '''
        output example:
        ['sky', 'bird']
        '''
        output = output.replace("', '", " ")
        output = output.replace("['", "")
        output = output.replace("']", "")
        output = output.replace("[]", "") # if empty
        print "New frame: " + output[:-1]
        

        return output
#        os.system(". ./runSTF.sh")

    def __init__(self, image_queue, output_queue):
        super(stfThread, self).__init__(image_queue, output_queue)
        self.imagecount = 0
        self.firstFrame = True

if __name__ == "__main__":

    image_queue = Queue.Queue(1)
    output_queue_list = list()

    stf_thread = None
    client_thread = None
    result_pub = None

    global energy_thread
    energy_thread = None

    try:
        energy_thread = EnergyRecordingThread("energy_log")
        sys.stdout.write("Finding control VM\n")
        service_list = get_service_list(sys.argv)
        video_ip = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_ADDRESS)
        video_port = service_list.get(SERVICE_META.VIDEO_TCP_STREAMING_PORT)
        return_addresses = service_list.get(SERVICE_META.RESULT_RETURN_SERVER_LIST)

        client_thread = AppProxyStreamingClient((video_ip, video_port), image_queue)
        client_thread.start()
        client_thread.isDaemon = True
        stf_thread = stfThread(image_queue, output_queue_list)
        stf_thread.start()
        stf_thread.isDaemon = True 
	
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
        if energy_thread != None:
            energy_thread.stop()
        if client_thread != None:
            client_thread.terminate()
        if stf_thread != None:
            stf_thread.terminate()
	if result_pub != None:
	    result_pub.terminate()

