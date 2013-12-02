import logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(message)s")

STRIDE0 = 1
STRIDE1 = 1
ALPHA = 0.5

import argparse
import numpy as np
import tempfile
import time
import zipfile

import pstf
from pstf.forest import normalize
from pstf.train_tree1 import load_forest_from_classifier, load_ndarray

from pstf import innerloop as innerloop_cy
from pstf import innerloop_ne
from pstf import innerloop_np
from pstf import innerloop_py

def load_from_classifier(classifier):
    print "Loading forest0"
    forest0 = load_forest_from_classifier(classifier, 'forest0.npz')
    hist0 = normalize(load_ndarray(classifier, 'hist0.npy'))
    hist0 = np.insert(hist0, 0, 0, axis=0)

    print "Loading forest1"
    forest1 = load_forest_from_classifier(classifier, 'forest1.npz')
    hist1 = normalize(load_ndarray(classifier, 'hist1.npy'))
    hist1 = np.insert(hist1, 0, 0, axis=0)

    print "Loading SVM models"
    training_bosts = normalize(load_ndarray(classifier, 'bosts.npy')).T
    NLABELS = hist0.shape[2]
    svmmodels = []
    f = zipfile.ZipFile(classifier)
    for i in range(1,NLABELS):
        model = f.read('svmmodel%d' % i)
        tmp = tempfile.NamedTemporaryFile()
        tmp.write(model)
        tmp.flush()
        svmmodels.append(pstf.svm_.load_model(tmp.name))
        tmp.close()
    f.close()
    return forest0, hist0, forest1, hist1, training_bosts, svmmodels

def compute_leafimage(name, tree, func):
    global groundtruth
    print name
    start = time.time()
    leafimage = func(tree, expanded_image, mask)
    print "Elapsed", time.time() - start
    if not (groundtruth == leafimage).all():
        print "RESULT DOES NOT MATCH GROUNDTRUTH"
        np.save('groundtruth.npy', groundtruth)
        np.save('leafimage-%s.npy' % name, leafimage)

parser = argparse.ArgumentParser()
parser.add_argument('-a', '--all', action='store_true')
parser.add_argument('--cython', action='store_true')
parser.add_argument('--numpy', action='store_true')
parser.add_argument('--python', action='store_true')
parser.add_argument('classifier')
parser.add_argument('testset')
args = parser.parse_args()

forest0, hist0, forest1, hist1, training_bosts, svmmodels = \
        load_from_classifier(args.classifier)
testtree = forest0.trees[0]

print "Loading image"
testset, classes = pstf.algum.load_dataset(args.testset, transforms='none')
image = testset[0]

print "Expanding image by %d pixels" % forest0.BORDER
expanded_image = pstf.forest.ExpandedImage(image.image, forest0.BORDER)
mask = np.ones(image.image.shape[:2], dtype='bool')

print "Computing groundtruth"
start = time.time()
groundtruth = testtree.compute_leafimage(expanded_image, mask)
print "Elapsed", time.time() - start

if args.all or args.cython:
    compute_leafimage("cython",  testtree, innerloop_cy.compute_leafimage)
if args.all or args.numpy:
    compute_leafimage("numpy",   testtree, innerloop_np.compute_leafimage)
if args.all or args.python:
    compute_leafimage("python",  testtree, innerloop_py.compute_leafimage)

