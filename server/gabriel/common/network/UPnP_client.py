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

import json
import os
import subprocess
import sys
import threading

import gabriel
LOG = gabriel.logging.getLogger(__name__)


class UPnPClientError(Exception):
    pass


class UPnPClient(threading.Thread):
    '''
    The UPnP client searches for a UPnP server and
    receives the registry address (@self.http_ip + @self.http_port)
    '''
    def __init__(self):
        self.stop = threading.Event()
        self.UPnP_bin = gabriel.Const.UPnP_CLIENT_PATH
        self.proc = None
        self.http_ip = None
        self.http_port = None
        self.service_list = None

        if os.path.exists(self.UPnP_bin) == False:
            raise UPnPClientError("Cannot find binary: %s" % self.UPnP_bin)
        threading.Thread.__init__(self, target = self.run_exec)

    def run_exec(self):
        cmd = ["java", "-jar", "%s" % (self.UPnP_bin)]
        LOG.info("execute : %s" % ' '.join(cmd))
        _PIPE = subprocess.PIPE
        self.proc = subprocess.Popen(cmd, stdout = _PIPE, stderr = _PIPE)

        while(not self.stop.wait(0.01)):
            self.proc.poll()
            if self.proc.returncode is None:
                continue

            if self.proc.returncode == 0:
                out = self.proc.stdout.read()
                out_list = out.strip().split("\n")
                for outline in out_list:
                    if len(outline.strip()) == 0:
                        continue
                    key, value = outline.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    if key.lower() == "ipaddress":
                        self.http_ip = str(value)
                    if key.lower() == "port":
                        self.http_port = int(value)
                break
            else:
                LOG.warning("Cannot locate Gabriel Service")
                break
        self.proc = None

    def terminate(self):
        self.stop.set()
        if self.proc != None:
            import signal
            self.proc.send_signal(signal.SIGINT)
            return_code = self.proc.poll()
            if return_code is not None and return_code != 0:
                msg = "UPnP client is closed unexpectedly: %d\n" % return_code
                LOG.warning(msg)


if __name__ == "__main__":
    try:
        UPnP_client = UPnPClient()
        UPnP_client.start()
        UPnP_client.join()
        LOG.info("Gabriel HTTP Meta Server is at %s:%d" % \
                (UPnP_client.http_ip, UPnP_client.http_port))

        #import time
        #time.sleep(20)
        #UPnP_client.terminate()
        #LOG.warning("Cannot find server")
    except UPnPClientError as e:
        LOG.error(str(e))

