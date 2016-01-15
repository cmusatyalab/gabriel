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
import time

import gabriel
LOG = gabriel.logging.getLogger(__name__)


class UPnPServerError(Exception):
    pass


class UPnPServer(threading.Thread):
    def __init__(self):
        self.stop = threading.Event()
        self.upnp_bin = gabriel.Const.UPnP_SERVER_PATH
        self.proc = None
        if not os.path.exists(self.upnp_bin):
            raise UPnPServerError("Cannot find UPnP server binary: %s" % self.upnp_bin)
        threading.Thread.__init__(self, target = self.run_exec)

    def run_exec(self):
        cmd = ["java", "-jar", "%s" % (self.upnp_bin)]
        _PIPE = subprocess.PIPE
        self.proc = subprocess.Popen(cmd, close_fds = True, stdin = _PIPE, stdout = _PIPE, stderr = _PIPE)

    def terminate(self):
        self.stop.set()
        if self.proc is not None:
            import signal
            self.proc.send_signal(signal.SIGINT)
            return_code = self.proc.poll()
            if return_code is None:
                self.proc.terminate()
            elif return_code is not None and return_code != 0:
                LOG.error("UPnP server closed unexpectedly. Return code is %d" % return_code)


if __name__ == '__main__':
    try:
        UPnP_server = UPnPServer()
        UPnP_server.start()
        while True:
            time.sleep(100)
    except KeyboardInterrupt as e:
        sys.stdout.write("Exit by user\n")
        UPnP_server.terminate()
        sys.exit(1)
    except UPnPServerError as e:
        print str(e)
        UPnP_server = None
