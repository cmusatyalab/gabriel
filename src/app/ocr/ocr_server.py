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

tr = Tesseract()

def run_ocr(image_data):
    buff = StringIO.StringIO()
    buff.write(image_data)
    buff.seek(0)
    image = Image.open(buff)
    utf8str = tr.ocr_image(image)
    return_str = utf8str.encode("ascii", "ignore")

    if return_str.isalpha():
        return return_str
    else:
        print "OCR result is not letter: %s" % return_str


if __name__ == "__main__":
    data = open('test.png', 'rb').read()
    print run_ocr(data)
