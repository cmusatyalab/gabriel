#
# STF filter, a searchlet for the OpenDiamond platform
#
# Copyright (c) 2011,2012 Carnegie Mellon University
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

from itertools import izip
import numpy as np

from .image import expand_border
from .innerloop import compute_leafimage

def normalize(bosts_or_histograms):
    N = bosts_or_histograms[0] + 0.00000001
    return np.true_divide(bosts_or_histograms, N)

def load_histogram(f):
    # - assume histogram is (NNODES, TREE_COUNT, NCLASSES)
    histogram = normalize(np.load(f))
    # - add a 'does not exist' node at index 0
    histogram = np.insert(histogram, 0, 0, axis=0)
    return histogram

def load_bosts(f):
    return normalize(np.load(f)).T

class LeafImage(object):
    def __init__(self, image, label, HISTLEN, STRIDE):
        self.image = image
        self.label = label
        self.TREE_COUNT = len(self.image)
        self.HISTLEN = HISTLEN
        self.STRIDE = STRIDE

    def compute_histogram(self, mask=None, sparse=False):
        image = self.image
        if mask is not None:
            image = image * mask

        hists = ( np.bincount(image[i].ravel(), minlength=self.HISTLEN)
                  for i in xrange(self.TREE_COUNT) )
        hists = np.vstack(hists).T

        if not sparse:
            tmp = hists
            while len(tmp) > 2:
                tmp = tmp.reshape(-1, 2, self.TREE_COUNT).sum(axis=1)
                hists[:len(tmp)] += tmp

        return hists[1:].T

    # segmentation step, map leafimage to per-class distribution
    def compute_distimage(self, histogram):
        assert(histogram.shape[:2] == (self.HISTLEN, self.TREE_COUNT))

        # map (TREE_COUNT, WIDTH, HEIGHT) leafimage against histogram to get
        #  (TREE, X, Y, CLASS FREQUENCY)
        imgs = ( histogram[self.image[i], i] for i in xrange(self.TREE_COUNT) )

        # sum across trees -> array(WIDTH, HEIGHT, CLASSES)
        return np.sum(imgs, axis=0)

    def compute_bost(self):
        return self.compute_histogram()[np.newaxis, ...].T

class ExpandedImage(object):
    def __init__(self, image, BORDER=0, IS_INTEGRAL=False, STRIDE=1):
        self.BORDER, self.STRIDE = BORDER, STRIDE
        self.BOTTOM, self.RIGHT = BORDER+image.shape[0], BORDER+image.shape[1]
        self.image = expand_border(image, BORDER, IS_INTEGRAL)

    def slice(self, x, y, c):
        BORDER, STRIDE = self.BORDER, self.STRIDE
        BOTTOM, RIGHT = self.BOTTOM, self.RIGHT
        return self.image[BORDER+y:BOTTOM+y:STRIDE,
                          BORDER+x:RIGHT+x:STRIDE, c]

    def get(self, x, dx, y, dy, c):
        BORDER, STRIDE = self.BORDER, self.STRIDE
        return self.image.item((BORDER+y*STRIDE+dy, BORDER+x*STRIDE+dx, c))

class Tree(object):
    def __init__(self, functions, fvalues, thresholds):
        self.functions = functions
        self.fvalues = fvalues
        self.thresholds = thresholds
    def compute_leafimage(self, image, mask0):
        return compute_leafimage(self, image, mask0)

class Forest(object):
    def __init__(self, functions=None, fvalues=None, thresholds=None,
                 TREE_COUNT=0, MAX_DEPTH=1, trees=None):
        if trees is not None:
            functions  = np.vstack([ tree.functions for tree in trees ])
            fvalues    = np.vstack([ tree.fvalues for tree in trees ])
            thresholds = np.vstack([ tree.thresholds for tree in trees ])

        elif functions is None:
            NSPLITS = (2**(MAX_DEPTH-1))-1
            functions  = np.zeros((TREE_COUNT, NSPLITS), dtype='uint8')
            fvalues    = np.zeros((TREE_COUNT, NSPLITS, 6), dtype='int16')
            thresholds = np.zeros((TREE_COUNT, NSPLITS), dtype='float')

        self.functions = functions
        self.fvalues = fvalues
        self.thresholds = thresholds

        self.NNODES = functions.shape[1]*2+1
        self.BORDER = 0
        self.recalc_border()

        self.trees = [ Tree(f, v, t)
                       for f, v, t in izip(functions, fvalues, thresholds) ]

    def recalc_border(self):
        self.BORDER = max(-self.fvalues.min(), self.fvalues.max())

    def compute_leafimage(self, labeledimage, STRIDE=1, mask0=True,
                          IS_INTEGRAL=False):
        # cast mask0 to shape of img
        rows, cols = labeledimage.image.shape[:2]
        mask = np.zeros((rows, cols), dtype='bool') | mask0

        img = ExpandedImage(labeledimage.image, self.BORDER,
                            IS_INTEGRAL, STRIDE)
        label = labeledimage.label

        # sample mask and labels if we are sampling the input image
        if STRIDE > 1:
            if label is not None:
                label = label[::STRIDE, ::STRIDE]
            mask = mask[::STRIDE, ::STRIDE]

        leafimage = np.dstack([ tree.compute_leafimage(img, mask).T
                                for tree in self.trees ]).T
        return LeafImage(leafimage, label, self.NNODES+1, STRIDE)

    def build_histogram(self, trainingset, NLABELS, STRIDE=1, IS_INTEGRAL=False,
                        progress=None):
        hist = np.zeros((NLABELS, len(self.trees), self.NNODES), dtype='int')

        for image in trainingset:
            if not isinstance(image, LeafImage):
                image = self.compute_leafimage(image, STRIDE, IS_INTEGRAL)

            cls = np.unique(image.label)
            for i in cls:
                if i == 0:
                    continue
                mask = (image.label == i)
                hist[i] += image.compute_histogram(mask)

            if progress:
                progress.update(1)
        return hist.T

    def build_bosts(self, trainingset, STRIDE=1, progress=None):
        NIMGS = len(trainingset)
        bosts = np.zeros((NIMGS, len(self.trees), self.NNODES), dtype='int')

        for i, image in enumerate(trainingset):
            if not isinstance(image, LeafImage):
                image = self.compute_leafimage(image, STRIDE)

            bosts[i] = image.compute_histogram()
            if progress: progress.update(1)
        return bosts.T

    def build_classes(self, trainingset, NLABELS, progress=None):
        NIMGS = len(trainingset)
        classes = np.zeros((NLABELS, NIMGS), dtype='bool')

        for i, image in enumerate(trainingset):
            cls = np.unique(image.label)
            classes[cls, i] = True
            if progress:
                progress.update(1)
        return classes

    def save(self, forest):
        np.savez(forest, functions=self.functions, fvalues=self.fvalues,
                 thresholds=self.thresholds)

    @classmethod
    def load(cls, forest):
        forest = np.load(forest)
        return cls(forest['functions'], forest['fvalues'], forest['thresholds'])

