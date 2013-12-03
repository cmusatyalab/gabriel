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
from multiprocessing import Pool 
import time

from ..forest import normalize
from ..image import convert_to_cielab, convert_to_primaries, make_palette, output_colormap, LabeledImage
from ..svm_ import load_model, make_vectors, svm_predict
from .train_tree1 import load_forest_from_classifier, load_ndarray, DistImage

STRIDE0 = 1
STRIDE1 = 1
ALPHA = 0.5
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

def calculate_class(key_frame_counter):
    image = Image.open(name + "%d.png" % (key_frame_counter % processes))
    #nx, ny = image.size
    #image = image.resize((int(nx/2), int(ny/2)), Image.ANTIALIAS)
    rgb = np.asarray(image)
    Lab = convert_to_cielab(rgb)
    pcv = convert_to_primaries(rgb, None)
    image = ImageLabelTuple(np.dstack((Lab, rgb, pcv)), None)

    print "Classifying/segmenting", name, key_frame_counter
    leafimage0 = forest0.compute_leafimage(image, STRIDE0)

    print "Computing SVM classification"
    bost = leafimage0.compute_bost()
    bost = normalize(bost).T
    vector = make_vectors(bost, training_bosts)
    svmresults = [0] + [ svm_predict(m, vector[0])
                         for m in svmmodels ]
    ILP = np.power(svmresults, ALPHA)
    print [(CLASSES[class_index], "%.02f" % score) for class_index, score in enumerate(ILP) if score > 0.7]

    #image.drop_caches()
    #sys.stdout = sys.__stdout__
    print


def main():
    import argparse
    import logging
    import os
    import yaml
    import cv

    parser = argparse.ArgumentParser()
    parser.add_argument('classifier')
    parser.add_argument('--postprocess', action="store_true",
                        help='Run postprocessing, close blobs and remove noise')
    parser.add_argument('dataset', help='Algum formatted trainingset or image')
    parser.add_argument('cores', type=int, help='Number of processes of paralellism')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s - %(message)s")

    global name
    name = os.path.splitext(args.dataset)[0] 

    classifier = zipfile.ZipFile(args.classifier)
    global forest0, svmmodels, training_bosts, hist0
    forest0, hist0, forest1, hist1, training_bosts, svmmodels, prior = \
        load_from_classifier(classifier)
    classifier.close()

    capture = cv.CaptureFromFile(args.dataset)
    frame_rate = cv.GetCaptureProperty(capture, cv.CV_CAP_PROP_FPS)
    total_frames = cv.GetCaptureProperty(capture, cv.CV_CAP_PROP_FRAME_COUNT)
    print frame_rate, total_frames
    
    global processes
    processes = args.cores
    print processes
    pool = Pool(processes = 1)

    KEY_FRAME_PERIOD = 2 # in seconds

    start_time = time.time()
    key_frame_counter_base = 0    
    frame = cv.QueryFrame(capture)
    while frame:
        process_num = 0
        while frame:
            cv.SaveImage(name + "%d.png" % process_num, frame)
            for i in xrange(int(KEY_FRAME_PERIOD * frame_rate)):
                frame = cv.QueryFrame(capture)
            process_num += 1
            if process_num == processes:
                break

        pool.map(calculate_class, range(key_frame_counter_base, key_frame_counter_base + process_num))
        
        key_frame_counter_base += process_num

    elapse_time = time.time() - start_time
    print "total time: %.2f, key frames: %d, frame per sec: %.2f" \
           % (elapse_time, key_frame_counter_base, key_frame_counter_base / elapse_time)


if __name__ == '__main__':
    main()

