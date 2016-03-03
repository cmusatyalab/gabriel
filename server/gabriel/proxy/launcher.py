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

import os
import subprocess
import sys
import threading


class AppLauncherError(Exception):
    pass


class AppLauncher(threading.Thread):

    def __init__(self, app_path, args = None, is_print = False):
        self.stop = threading.Event()
        self.app_binary = os.path.abspath(app_path)
        self.args = args
        self.is_print = is_print
        self.proc = None
        self.ip_addr = None
        self.port_number = None

        if os.path.exists(self.app_binary) == False:
            raise AppLauncherError("Cannot find binary: %s" % self.app_binary)
        threading.Thread.__init__(self, target = self.run_exec)

    def run_exec(self):
        cmd = [self.app_binary]
        if self.args is not None:
            cmd += self.args
        sys.stdout.write("execute : %s\n" % ' '.join(cmd))
        _PIPE = subprocess.PIPE
        if self.is_print:
            self.proc = subprocess.Popen(cmd, close_fds = True)
        else:
            self.proc = subprocess.Popen(cmd, close_fds = True, stdout = _PIPE, stderr = _PIPE)

        while (not self.stop.wait(0.01)):
            self.proc.poll()
            if self.proc.returncode is not None:
                if self.proc.returncode == 0:
                    break
                else:
                    msg = "Application terminate unexpectly:%d\n" % self.proc.returncode
                    sys.stdout.write(msg)
                    break
        self.proc = None

    def terminate(self):
        self.stop.set()
        if self.proc is not None:
            import signal
            self.proc.send_signal(signal.SIGINT)
            return_code = self.proc.poll()
            if return_code is not None and return_code != 0:
                msg = "Application is closed unexpectedly: %d\n" % return_code
                sys.stdout.write(msg + "\n")
