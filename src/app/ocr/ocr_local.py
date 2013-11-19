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

from tesserwrap import Tesseract
from PIL import Image
import StringIO
import SocketServer
from optparse import OptionParser
import sys
import threading
import time

tr = Tesseract(lang="eng")
tr.set_page_seg_mode(3)


def run_ocr(image_data, force_return=False):
    buff = StringIO.StringIO()
    buff.write(image_data)
    buff.seek(0)
    image = Image.open(buff)
    tr.set_image(image)
    utf8str = tr.get_utf8_text()
    return_str = utf8str.encode("ascii", "ignore")
    #return_str = utf8str

    if force_return:
        return return_str
    else:
        if return_str.isalpha():
            return return_str
        else:
            print "OCR result is not letter"


def process_command_line(argv):
    VERSION = "OCR test 0.1"

    parser = OptionParser(usage='%prog [option]', version=VERSION, 
            description="OCR Test")

    parser.add_option(
            '-i', '--input', action='store', dest='image_dir',
            help="image dir for testing")
    settings, args = parser.parse_args(argv)
    if len(args) >= 1:
        parser.error("invalid arguement")

    if hasattr(settings, 'image_dir') and settings.image_dir is not None:
        if os.path.isdir(settings.image_dir) is False:
            parser.error("%s is not a directory" % settings.image_dir)
    return settings, args


if __name__ == "__main__":
    import os
    from os import listdir

    settings, args = process_command_line(sys.argv[1:])
    image_dir = settings.image_dir
    filelist = [os.path.join(image_dir, f) for f in listdir(image_dir)
            if f.lower().endswith("jpeg") or f.lower().endswith("jpg")]
    filelist.sort()
    execution_time =list()
    for image in filelist:
        data = open(image, 'rb').read()
        s_time = time.time()
        run_ocr(data, force_return=True)
        e_time = time.time()
        print "%s\t(%f-%f=%f)" % (image, e_time, s_time, e_time-s_time)
        execution_time.append(e_time-s_time)

    time_sum = 0.0
    for item in execution_time:
        time_sum += item

    print "average time: %f" % (time_sum/len(execution_time))
