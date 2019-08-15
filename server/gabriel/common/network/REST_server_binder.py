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

import os
import subprocess
import sys
import threading

dir_file = os.path.dirname(os.path.realpath(__file__))

sys.path.insert(0, os.path.join(dir_file, "../../.."))
import gabriel
LOG = gabriel.logging.getLogger(__name__)


class RESTServerError(Exception):
    pass


class RESTServer(threading.Thread):
    def __init__(self):
        self.stop = threading.Event()
        self.proc = None

        # find the REST server binary
        file_path = os.path.dirname(os.path.abspath(__file__))
        self.REST_bin = os.path.abspath(os.path.join(file_path, "REST_server.py"))
        if not os.path.exists(self.REST_bin):
            raise RESTServerError("Cannot find REST server binary: %s" % self.REST_bin)

        self.net_interface = "eth0" # default
        self.ip_addr = gabriel.network.get_ip(self.net_interface)
        threading.Thread.__init__(self, target = self.run_exec)

    def run_exec(self):
        cmd = ["python", "%s" % (self.REST_bin), '-n', self.net_interface, '-s', self.ip_addr]
        #_PIPE = subprocess.PIPE
        _PIPE = None
        self.proc = subprocess.Popen(cmd, close_fds = True,
                stdin = _PIPE, stdout = _PIPE, stderr = _PIPE)
        try:
            while (not self.stop.wait(1)):
                self.proc.poll()
                if self.proc.returncode is not None:
                    if self.proc.returncode == 0:
                        LOG.info("REST API server has finished")
                        self.proc = None
                        break
                    if self.proc.returncode != 0:
                        LOG.error("REST API server closed unexpectedly. Return code is %d" % self.proc.returncode)
                        self.proc = None
                        break
        except KeyboardInterrupt as e:
            pass

    def set_interface(self, interface):
        self.net_interface = interface

    def set_ip(self, ip):
        self.ip_addr = ip

    def terminate(self):
        self.stop.set()
        try:
            if self.proc is not None:
                import signal
                self.proc.send_signal(signal.SIGINT)
                self.proc.poll()
                if self.proc.returncode is None:
                    self.proc.terminate()
                elif self.proc.returncode != 0:
                    LOG.error("REST API server closed unexpectedly. Return code is %d" % self.proc.returncode)
        except Exception as e:
            pass


if __name__ == '__main__':
    try:
        REST_server = RESTServer()
        REST_server.start()
    except RESTServerError as e:
        print(str(e))
        REST_server = None
