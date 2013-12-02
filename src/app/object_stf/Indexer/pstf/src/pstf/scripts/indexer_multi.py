#
# STF filter, a searchlet for the OpenDiamond platform
#
# Copyright (c) 2012 Carnegie Mellon University
#
# This filter is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.
#
# This filter is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License.
# If not, see <http://www.gnu.org/licenses/>.
#

from PIL import Image, ImageColor
from collections import namedtuple
import itertools
import json
import numpy as np
import pymorph
import sys
import tempfile
import zipfile
from multiprocessing import Queue, Process, Manager
#import Queue
#import threading
import time
import cv
import os

from ..forest import normalize
from ..image import convert_to_cielab, convert_to_primaries, make_palette, output_colormap, LabeledImage
from ..svm_ import load_model, make_vectors, svm_predict
from .train_tree1 import load_forest_from_classifier, load_ndarray, DistImage

STRIDE0 = 1
STRIDE1 = 1
ALPHA = 0.5
SCORE_THRESHOLD = 0.7
CLASSES = [
        "unlabeled", 
        "building", 
        "grass", 
        "tree", 
        "cow", 
        "sheep", 
        "sky", 
        "aeroplane", 
        "water", 
        "face", 
        "car", 
        "bicycle", 
        "flower", 
        "sign", 
        "bird", 
        "book", 
        "chair", 
        "road", 
        "cat", 
        "dog", 
        "body", 
        "boat"
    ]
VIDEO_RESOURCE = "/cloudletstore/segments/videos"

ImageLabelTuple = namedtuple('ImageLabelTuple', ['image', 'label'])

def load_from_classifier(classifier):
    forest0 = load_forest_from_classifier(classifier, 'forest0.npz')
    hist0 = load_ndarray(classifier, 'hist0.npy')
    prior = np.true_divide(hist0[0].sum(axis=0), hist0[0].sum())
    hist0 = np.insert(normalize(hist0), 0, 0, axis=0)

    forest1 = load_forest_from_classifier(classifier, 'forest1.npz')
    hist1 = load_ndarray(classifier, 'hist1.npy')
    hist1 = np.insert(normalize(hist1), 0, 0, axis=0)

    svmmodels = []
    try:
        training_bosts = normalize(load_ndarray(classifier, 'bosts.npy')).T

        NLABELS = hist0.shape[2]
        for i in range(1, NLABELS):
            model = classifier.read('svmmodel%d' % i)
            tmp = tempfile.NamedTemporaryFile()
            tmp.write(model)
            tmp.flush()
            svmmodels.append(load_model(tmp.name))
            tmp.close()
    except KeyError:
        training_bosts = None
    return forest0, hist0, forest1, hist1, training_bosts, svmmodels, prior

def calculate_class(name, queue, data_queue):
    while not data_queue.empty():
        try:
            frame_counter = data_queue.get()
        except Empty:
            break
        image = Image.open("tmp/" + name + "%d.png" % frame_counter)
        #image = image.resize((854, 480), Image.ANTIALIAS)
        image = image.resize((640, 360), Image.ANTIALIAS)
        rgb = np.asarray(image)
        Lab = convert_to_cielab(rgb)
        pcv = convert_to_primaries(rgb, None)
        image = ImageLabelTuple(np.dstack((Lab, rgb, pcv)), None)
 
        print "Classifying/segmenting", name, frame_counter
        leafimage0 = forest0.compute_leafimage(image, STRIDE0)

        print "Computing SVM classification"
        bost = leafimage0.compute_bost()
        bost = normalize(bost).T
        vector = make_vectors(bost, training_bosts)
        svmresults = [0] + [ svm_predict(m, vector[0])
                             for m in svmmodels ]
        ILP = np.power(svmresults, ALPHA)
        result = [(CLASSES[class_index], "%.02f" % score) for class_index, score in enumerate(ILP) if score > SCORE_THRESHOLD]
        print result
        queue.put([frame_counter, ILP])
        print


def main():
    import argparse
    import logging
    import os
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument('classifier')
    parser.add_argument('--postprocess', action="store_true",
                        help='Run postprocessing, close blobs and remove noise')
    parser.add_argument('videolist', help='A file listed all the videos to be indexed')
    parser.add_argument('cores', type=int, help='Number of processes of paralellism')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s - %(message)s")

    classifier = zipfile.ZipFile(args.classifier)
    global forest0, svmmodels, training_bosts, hist0
    forest0, hist0, forest1, hist1, training_bosts, svmmodels, prior = \
        load_from_classifier(classifier)
    classifier.close()

    KEY_FRAME_PERIOD = 2 # in seconds
    #queue = Queue.Queue()
    #data_queue = Queue.Queue()
    queue = Manager().Queue()
    data_queue = Manager().Queue()

    for processes in [4]:    
        video_list = open(args.videolist, 'r')
        log_file = open('statistics%d.txt' % processes, 'w')

        fps = 0
        fps_count = 0

        for video_file in video_list:
            video_file = video_file.strip()
            name = os.path.splitext(video_file)[0]
            file_path = os.path.join(VIDEO_RESOURCE, video_file)
            log_file.write(file_path+"\n")

            capture = cv.CaptureFromFile(file_path)
            frame_rate = cv.GetCaptureProperty(capture, cv.CV_CAP_PROP_FPS)
            total_frames = cv.GetCaptureProperty(capture, cv.CV_CAP_PROP_FRAME_COUNT)
            log_file.write("frame rate: %.3f, total frames: %d\n" % (frame_rate, total_frames)) 

            start_time0 = time.time()
            key_frame_counter = 0    
            frame = cv.QueryFrame(capture)
            os.makedirs("tmp")
            while frame:
                cv.SaveImage("tmp/" + name + "%d.png" % key_frame_counter, frame)
                for i in xrange(int(KEY_FRAME_PERIOD * frame_rate)):
                    frame = cv.QueryFrame(capture)
                key_frame_counter += 1
            for i in xrange(key_frame_counter):
                 data_queue.put(i)

            start_time = time.time()

            ps = []
            for group in xrange(processes):
                p = Process(target = calculate_class, args=(name, queue, data_queue, ))
                #p = threading.Thread(target = calculate_class, args=(name, queue, data_queue, ))
                p.start()
                ps.append(p)
            for p in ps:
                p.join()

            elapse_time = time.time() - start_time

            accuracy_file = open('360.txt', 'w')
            while not queue.empty():
                q_entry = queue.get()
                frame_counter = q_entry[0]
                ILP = q_entry[1]
                accuracy_file.write('%d' % frame_counter)
                for class_index, score in enumerate(ILP):
                    accuracy_file.write(',%.02f' % score)
                accuracy_file.write('\n')
            accuracy_file.close()

            os.system("rm -rf tmp")

            log_file.write("decoding time: %.2f, total time: %.2f, key frames: %d, frame per sec: %.3f\n" \
                % (start_time - start_time0, elapse_time, key_frame_counter, key_frame_counter / elapse_time))
            fps += key_frame_counter / elapse_time
            fps_count += 1

            #time.sleep(10)

        video_list.close()
        log_file.write("average fps: %.3f\n" % (fps/fps_count))
        log_file.close()

if __name__ == '__main__':
    main()

