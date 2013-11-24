#!/usr/bin/env python
#
# Cloudlet Infrastructure for Mobile Computing
#
#   Author: Zhuo Chen <zhuoc@cs.cmu.edu>
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
sys.path.insert(0, "lib")
import svmutil
import os
import time
import subprocess
import shutil
import Image
import cv
import cv2
from cStringIO import StringIO

TMP_DIR = "tmp"
if not os.path.isdir(TMP_DIR):
    os.makedirs(TMP_DIR)
bin_path = "bin"
centers_path = "cluster_centers"
model_path = "models"

w = 160
h = 120
video_path = "%s/tmp.avi" % TMP_DIR
video_name = os.path.basename(video_path).split('.avi')[0]
selected_feature = "mosift"
descriptor = "all"
n_clusters = 1024

model_names = ["SayHi", "Clapping", "TurnAround", "Squat", "ExtendingHands"]
svmmodels = []
for model_name in model_names:
    svmmodels.append(svmutil.svm_load_model("%s/model_%s_%s_%s_%d" % (model_path, model_name, selected_feature, descriptor, n_clusters)))
MESSAGES = ["Someone is waving to you.", "Someone is clapping.", "Someone is turning around.", "Someone just squated.", "Someone wants to shake hands with you."]


def _format_convert(data):
    file_jpgdata = StringIO(data)
    image = Image.open(file_jpgdata)
    frame_bgr = cv.CreateImageHeader(image.size, cv.IPL_DEPTH_8U, 3)
    cv.SetData(frame_bgr, image.tostring())
    # convert frame from BGR to RGB
    # in Image, it's RGB; in cv, it's BGR
    frame = cv.CreateImage(cv.GetSize(frame_bgr),cv.IPL_DEPTH_8U, 3)
    cv.CvtColor(frame_bgr, frame, cv.CV_BGR2RGB)
    return frame

def extract_feature(images):
    # Write into a video chunk
    #fd, video_path = tempfile.mkstemp(dir='tmp/all', prefix='image_pair_', suffix='.avi')
    videoWriter = cv2.VideoWriter(video_path, cv.CV_FOURCC('X', 'V', 'I', 'D'), 30, (160, 120), True)
    for frame in images:
        videoWriter.write(frame)

    # extract features
    raw_file = "%s/%s_raw_%s.txt" % (TMP_DIR, selected_feature, video_name)
    txyc_file = "%s/%s_%s_%d_%s.txyc" % (TMP_DIR, selected_feature, descriptor, n_clusters, video_name)
    tmp_video_file = "%s/%s_%s.avi" % (TMP_DIR, selected_feature, video_name)
    #stable_video_file = "%s/stable_%s_%s.avi" % (TMP_DIR, selected_feature, video_name)
    #input_file = "%s/input_%s_%s.txt" % (TMP_DIR, selected_feature, video_name)
    center_file = "%s/centers_%s_%s_%d" % (centers_path, selected_feature, descriptor, n_clusters)

    DEVNULL = open(os.devnull, 'wb')
    subprocess.call(['avconv', '-i', video_path, '-c:v', 'libxvid', '-s', '%dx%d' % (w,h),
                     '-r', '30', tmp_video_file], stdout=DEVNULL, stderr=DEVNULL)
    with open(raw_file, 'wb') as out:
        if selected_feature == "mosift":
            subprocess.call(['%s/siftmotionffmpeg' % bin_path, '-r', '-t', '1', '-k', '2',
                             tmp_video_file, raw_file], stdout=DEVNULL, stderr=DEVNULL)
    subprocess.call(['%s/txyc' % bin_path, center_file, str(n_clusters), raw_file, txyc_file, selected_feature, descriptor], stdout=DEVNULL, stderr=DEVNULL)
    DEVNULL.close()

    with open(txyc_file) as f:
        result = f.readlines()

    os.remove(video_path)
    os.remove(tmp_video_file)
    os.remove(raw_file)
    os.remove(txyc_file)

    return result

def load_data(spbof_file):
    with open(spbof_file, 'r') as f:
        v_data = f.readline().strip().split()
        v = {}
        for v_dim in v_data:
            v_tmp = v_dim.split(':')
            idx = int(v_tmp[0])
            val = float(v_tmp[1])
            v[idx] = val
    return v

def classify(feature_list):
    DEVNULL = open(os.devnull, 'wb')
    txyc_file = "%s/%s_%s_%d_%s.txyc" % (TMP_DIR, selected_feature, descriptor, n_clusters, video_name)
    spbof_file = "%s/%s_%s_%d_%s.spbof" % (TMP_DIR, selected_feature, descriptor, n_clusters, video_name)
    with open(txyc_file, 'w') as f:
        for feature in feature_list:
            f.write(feature)
    subprocess.call(['%s/spbof' % bin_path, txyc_file, str(w), str(h), str(n_clusters), '10', spbof_file, '1'], stdout=DEVNULL, stderr=DEVNULL)   
    os.remove(txyc_file)
    DEVNULL.close()

    # Detect activity from MoSIFT feature vectors
    feature_vec = load_data(spbof_file)
    os.remove(spbof_file)

    model_idx = -1
    max_score = 0
    for idx, model_name in enumerate(model_names):
        if idx == len(model_names) - 1:  # exclude "ExtendingHands"
            break
        p_labs, p_acc, p_vals = svmutil.svm_predict([1], [feature_vec], svmmodels[idx], "-b 1 -q")
        labels = svmmodels[idx].get_labels()
        val = p_vals[0][0] * labels[0] + p_vals[0][1] * labels[1]
        if val > max_score:
            max_score = val 
            model_idx = idx

    model_name = model_names[model_idx]
    if max_score > 0.5: # Activity is detected
        current_time = time.time()    
        print "ACTIVITY DETECTED: %s!" % model_name
        print "Current time: %f" % current_time
        print "Confidence score: %f" % max_score

        MESSAGE = MESSAGES[model_idx]
        return MESSAGE
    else:
        print "Max confidence score: %f, activity is: %s" % (max_score, model_name)
        return "nothing"
