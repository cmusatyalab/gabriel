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

import threading
import psutil
import sys
import time


class UsageMonitorError(Exception):
    pass


class UsageMonitor(threading.Thread):

    def __init__(self):
        self.outfile = open("./monitor-%d.txt" % int(time.time()), "wb")
        self.stop = threading.Event()
        self.cpu_avg = 0.0
        self.mem_avg = 0.0
        threading.Thread.__init__(self, target=self.monitor)

    def monitor(self):
        count = 1
        while(not self.stop.wait(0.5)):
            cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            self.cpu_avg += cpu_percent
            self.mem_avg += memory.percent
            log = "[usage]\t%f\tcpu\t%s\t%s\t%f\t%f\n" %\
                    (time.time(), str(cpu_percent), str(memory),\
                    self.cpu_avg/count, self.mem_avg/count)
            sys.stdout.write(log)
            if self.outfile is not None:
                self.outfile.write(log)
                self.outfile.flush()
            
            count += 1

    def terminate(self):
        self.stop.set()
        if self.outfile is not None:
            self.outfile.close()
            self.outfile = None


if __name__ == "__main__":
    thread = None
    exit_status = 1
    try:
        thread = UsageMonitor()
        thread.start()
        sys.stdout.write("Usage Monitor Start\n")
        while True:
            time.sleep(100)

        #import time
        #time.sleep(20)
        #upnp_client_thread.terminate()
        #LOG.warning("Cannot find server")
    except UsageMonitorError as e:
        sys.stderr.write(str(e))
        exit_status = 1
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        exit_status = 0
    finally:
        if thread is not None:
            thread.terminate()
        sys.exit(exit_status)

