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
sys.path.insert(0, "../common")
import time
import Queue

from app_proxy import AppProxyStreamingClient
from app_proxy import AppProxyThread


class DummyApp(AppProxyThread):
    def handle(self, header, data):
        return None


if __name__ == "__main__":
    sys.stdout.write("Start OCR proxy\n")
    image_queue = Queue.Queue(1)
    output_queue = Queue.Queue()
    control_addr = ("128.2.210.197", 10101)
    client = AppProxyStreamingClient(control_addr, image_queue, output_queue)
    client.start()
    client.isDaemon = True
    app_thread = DummyApp(image_queue, output_queue)
    app_thread.start()
    app_thread.isDaemon = True

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt as e:
        sys.stdout.write("user exits\n")
        client.terminate()
        app_thread.terminate()
    except Exception as e:
        client.terminate()
        app_thread.terminate()

