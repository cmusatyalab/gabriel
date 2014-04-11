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

import subprocess
import threading
import os
import sys
from config import Const as Const


class RESTServerError(Exception):
    pass


class RESTServer(threading.Thread):
    def __init__(self):
        self.stop = threading.Event()
        self.REST_bin = Const.REST_SERVER_BIN
        self.proc = None
        if os.path.exists(self.REST_bin) == False:
            raise RESTServerError("Cannot find binary: %s" % self.REST_bin)
        threading.Thread.__init__(self, target=self.run_exec)

    def run_exec(self):
        cmd = ["python", "%s" % (self.REST_bin)]
        _PIPE = subprocess.PIPE
        self.proc = subprocess.Popen(cmd, close_fds=True, \
                stdin=_PIPE, stdout=_PIPE, stderr=_PIPE)
        try:
            while(not self.stop.wait(10)):
                self.proc.poll()
                return_code = self.proc.returncode
                if return_code != None:
                    if return_code == 0:
                        self.proc = None
                        break
                    if return_code != 0:
                        msg = "[Error] RESTful API Server is closed unexpectedly\n"
                        sys.stderr.write(msg)
                        break
        except KeyboardInterrupt, e:
            pass

    def terminate(self):
        self.stop.set()
        try:
            if self.proc != None:
                import signal
                self.proc.send_signal(signal.SIGINT) 
                self.proc.wait()
                if self.proc.returncode == None:
                    self.proc.terminate()
                elif self.proc.returncode != 0:
                    msg = "[Error] RESTful Server closed unexpectedly: %d\n" % \
                            self.proc.returncode
        except Exception as e:
            pass


if __name__ == '__main__':
    try:
        rest_server = RESTServer()
        rest_server.start()
    except RESTServerError as e:
        print str(e)
        rest_server = None
