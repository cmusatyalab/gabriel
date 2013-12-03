# -*- encoding: utf-8 -*-
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
import logging
import math
import numpy as np
import random

from . import innerloop
from .forest import Forest
from .utils import FUNCTIONS, DIRICHLET, Progress

log = logging.getLogger("pstf.trainer")

LEAF = FUNCTIONS.index('leaf')
VAL = FUNCTIONS.index('val')
ADD = FUNCTIONS.index('add')
SUB = FUNCTIONS.index('sub')
ABS = FUNCTIONS.index('abs')
BOX = FUNCTIONS.index('box')

#
# Generate candidate functions for training STF forest0
#
def Forest0Candidate(PATCH_SIZE, MAX_COLOR_CHANNELS):
    forest0_functions = [ VAL, ADD, SUB, ABS ]

    def generate_candidate():
        f = random.choice(forest0_functions)
        x1 = random.randint(-(PATCH_SIZE/2), PATCH_SIZE/2)
        y1 = random.randint(-(PATCH_SIZE/2), PATCH_SIZE/2)
        c1 = random.randrange(0, MAX_COLOR_CHANNELS)
        if f != VAL:
            x2 = random.randint(-(PATCH_SIZE/2), PATCH_SIZE/2)
            y2 = random.randint(-(PATCH_SIZE/2), PATCH_SIZE/2)
            c2 = random.randrange(0, MAX_COLOR_CHANNELS)
        else:
            x2 = y2 = c2 = 0
        return innerloop.Candidate(f, x1, y1, c1, x2, y2, c2)
    return generate_candidate

#
# Generate candidate functions for training STF forest1
#
def Forest1Candidate(BOX_SIZE, MAX_ROWS, MAX_COLS, STRIDE, MAX_CLASSES):
    forest1_functions = [ BOX ]
    #BOX_SIZE /= STRIDE
    #MAX_ROWS /= STRIDE
    #MAX_COLS /= STRIDE

    def generate_candidate():
        f = forest1_functions[0] # random.choice(forest1_functions)
        rows = random.randrange(0, MAX_ROWS)
        cols = random.randrange(0, MAX_COLS)
        row = random.randrange(-BOX_SIZE, BOX_SIZE-rows+1)
        col = random.randrange(-BOX_SIZE, BOX_SIZE-cols+1)
        chan = random.randrange(0, MAX_CLASSES)

        x1, x2 = col, col+cols
        y1, y2 = row, row+rows
        return innerloop.Candidate(f, x1, y1, chan, x2, y2, chan)
    return generate_candidate


def gen_thresholds(values, THRESHOLDS):
    mean, stdev = np.mean(values), np.std(values) or 1
    thresholds = np.random.normal(mean, stdev, (THRESHOLDS+1,))
    thresholds[0] = mean # not sure why, but original code wanted mean in there
    return thresholds

def try_threshold(split, classes, weights):
    # calculate information gained
    if split.all() or not split.any():
        return np.NINF

    NCLASSES = len(weights)
    clss = classes + (split * NCLASSES)
    hist = np.bincount(clss, minlength=2*NCLASSES).reshape(2, -1) * weights
    sums = hist.sum(axis=1)[..., np.newaxis]
    s0, s1 = sums.item(0), sums.item(1)

    # in the following we do some swapping around to avoid allocating extra
    # temporaries but the actual intent is,
    #   probabilities = histograms / histograms.sum()
    #   entropy = probabilities * log2(probabilities) if probability != 0 else 0
    #   entropy = -entropy.sum() #== shannon's entropy
    #   expected_gain = -(entropy*sums) / sums.sum()
    #                 == -(|Il|/|In|)*El - (|Ir|/|In|)*Er
    hist += DIRICHLET / NCLASSES
    sums += DIRICHLET
    hist /= sums # class probabilities
    hist *= -np.log2(hist)
    entropy = hist.sum(axis=1)[..., np.newaxis]
    e0, e1 = entropy.item(0), entropy.item(1)

    expected_gain = -(s0*e0 + s1*e1) / (s0+s1)

    #log.debug("expected gain %f (%f,%f) (%f,%f)", expected_gain,s0,s1,e0,e1)
    return expected_gain


def train_treenode(datapoints, weights, candidate, FEATURES, THRESHOLDS):
    classes = np.empty_like(datapoints, dtype='int')
    for i in xrange(datapoints.shape[0]):
        classes[i] = datapoints[i].label

    # if everything is of a single class, we cannot split the datapoints
    if len(np.unique(classes)) <= 1:
        return None, None

    values = np.empty_like(datapoints, dtype='float')
    split = np.empty_like(datapoints, dtype='bool')

    expected_gain = None
    for i in xrange(FEATURES):
        node = candidate()
        values = node.vfunc(datapoints, out=values)

        thresholds = gen_thresholds(values, THRESHOLDS)
        for t in thresholds:
            np.less_equal(values, t, out=split)
            gain = try_threshold(split, classes, weights)
            if gain > expected_gain:
                leader = node
                leader.threshold = t
                expected_gain = gain

    return leader, expected_gain


def train_forest(datapoints, weights, candidate, FEATURES, THRESHOLDS,
                 NTREES, MAX_DEPTH):
    forest = Forest(TREE_COUNT=NTREES, MAX_DEPTH=MAX_DEPTH)
    datapoints = np.asarray(datapoints)
    weights[0] = 0 # unlabeled datapoints do not contribute to information gain

    WORK_UNITS = len(datapoints) * NTREES * (MAX_DEPTH-1)
    progress = Progress(total_units=WORK_UNITS, incremental=True)
    PRUNED = 0

    for tree in forest.trees:
        points = [ datapoints ]

        for i in xrange(tree.functions.shape[0]):
            depth = int(math.log(i+1, 2))+1
            dps = points[i]
            points[i] = None

            NPOINTS = len(dps) if dps is not None else 0
            if NPOINTS < 5:
                log.debug("pruned, not enough datapoints")
                points.extend([None, None])
                PRUNED += NPOINTS * (MAX_DEPTH - depth)
                continue

            node, gain = train_treenode(dps, weights, candidate,
                                        FEATURES, THRESHOLDS)
            if node is None:
                log.debug("pruned, not enough classes")
                points.extend([None, None])
                PRUNED += NPOINTS * (MAX_DEPTH - depth)
                continue

            tree.functions[i] = node.function
            tree.fvalues[i] = node.fvalues
            tree.thresholds[i] = node.threshold

            split = node.vfunc(dps) <= node.threshold
            splitl, splitr = dps[split], dps[~split]
            points.extend([splitl, splitr])

            if log.isEnabledFor(logging.INFO):
                log.info("%d:%s %f (%s | %s)", depth, FUNCTIONS[node.function],
                         gain, len(splitl), len(splitr))

            progress.update(NPOINTS + PRUNED)
            PRUNED = 0

    progress.done()
    forest.recalc_border()
    return forest

