# -*- coding: utf-8 -*-
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

import ctypes
import itertools
import logging
import math
import numpy as np
try:
    import svm, svmutil
except ImportError:
    from libsvm import svm, svmutil

from .utils import libc_srand, memoize

# Parameters for searching for best C and γ training parameters
C_BEGIN, C_END = -5.0,  15.0
G_BEGIN, G_END = -15.0, 3.0
COARSE_STEP, FINE_STEP = 2.0, 0.25
FOLD = 5

log = logging.getLogger('pstf.svm')

def load_model(f):
    return svmutil.svm_load_model(f)

@memoize
def _splits_n_weights(MAX_DEPTH):
    splits = 2 ** np.arange(1, MAX_DEPTH) - 1
    weights = 2.0 ** np.arange(MAX_DEPTH-1, -1, -1)
    return splits, weights

def _score(M, splits, weights):
    sums = [ a.sum(axis=2) for a in np.split(M, splits, axis=2) ]
    scores = np.sum(sums, axis=2).T / weights
    return scores


def make_vectors(test, train, progress=None):
    MAX_DEPTH = int(math.ceil(math.log(train.shape[2], 2)))
    splits, weights = _splits_n_weights(MAX_DEPTH)
    test = np.asarray(test)
    test_scores = _score(test, splits, weights)
    if not np.array_equal(test, train):
        train_scores = _score(train, splits, weights)
    else:
        train_scores = test_scores

    vectors = np.zeros((len(test), len(train)))

    incr = 1.0 / len(test)
    for i, P, PPscore in itertools.izip(itertools.count(), test, test_scores):
        PQs = np.minimum(P, train)
        PQ_scores = _score(PQs, splits, weights)
        PQ_scores /= np.sqrt(PPscore * train_scores)
        np.mean(PQ_scores, axis=1, out=vectors[i])
        if progress:
            progress.update(incr)

    indices = np.expand_dims(np.arange(1, len(test)+1), axis=0).T
    return np.hstack((indices, vectors))


#def svm_predict(model, vector):
#    nodearray,_ = svm.gen_svm_nodearray(vector.tolist())
#    return svm.libsvm.svm_predict(model, nodearray) > 0

def svm_predict(model, vector):
    nodearray, _ = svm.gen_svm_nodearray(vector.tolist())
    nclass = model.get_nr_class()
    estimates = (ctypes.c_double * nclass)()
    _ = svm.libsvm.svm_predict_probability(model, nodearray, estimates)
    try:
        index = model.get_labels().index(1)
        return estimates[index]
    except ValueError:
        return 0.0


def _pyramid_match_kernel(P, Q, splits, weights):
    def _compress(M, splits):
        return [ a.sum(axis=1) for a in np.split(M, splits, axis=1) ]

    # PART 1:
    # Generate matrices of all the histogram intersections we'll need to
    # compute the similarity between two bosts.
    #
    # Make a TxL matrix where T is the # of trees and L is the # of
    # levels The ith row and jth column represents the sum of all mins
    # of the values of corresponding nodes from P and Q in tree i at
    # level j. (see histogram intersection).
    #
    # Simultaneously calculate the same TxL matrix for P with P and Q
    # with Q. In these cases the min is unnecessary (min(x,x) == x).
    #
    PQ = np.dstack((P, Q)).min(axis=2)

    PP = _compress(P, splits)
    QQ = _compress(Q, splits)
    PQ = _compress(PQ, splits)

    # PART 2:
    # Use these matrices to quickly compute similarity scores.
    PPscore = np.sum(PP, axis=1) / weights
    QQscore = np.sum(QQ, axis=1) / weights
    PQscore = np.sum(PQ, axis=1) / weights

    return np.mean(PQscore / np.sqrt(PPscore * QQscore))


def range_f(begin, end, step):
    """similar to xrange() but includes the end value and can handle floats"""
    while begin <= end:
        yield begin
        begin += step


def _grid_search(problem, C_RANGE, G_RANGE, progress=None,
                 best_rate=-1, best_c1=None, best_g1=None):
    log.info('Grid search over C=(%.3f:%.3f), γ=(%.3f:%.3f)',
             C_RANGE[0], C_RANGE[1], G_RANGE[0], G_RANGE[1])

    crange = list(range_f(*C_RANGE))
    grange = list(range_f(*G_RANGE))
    incr = 0.5 / (len(crange) * len(grange))

    for c1, g1 in itertools.product(crange, grange):
        params = svm.svm_parameter('-q -t 4 -b 1 -c %f -g %f -v %d' %
                                   (2.0**c1, 2.0**g1, FOLD))

        libc_srand(0) # reset random seed between runs
        # svmutil.svm_train is a bit chatty when cross_validating even with '-q'
        #rate = svmutil.svm_train(problem, params), c1, g1

        svm.libsvm.svm_set_print_string_function(params.print_func)
        svm.libsvm.svm_check_parameter(problem, params)

        results = (ctypes.c_double * problem.l)()
        svm.libsvm.svm_cross_validation(problem, params, FOLD, results)
        correct_results = 0
        for y, v in zip(problem.y, results):
            if y == v:
                correct_results += 1
        rate = 100.0 * correct_results / problem.l

        log.debug("(%.3f,%.3f)=%f", c1, g1, rate)
        if (rate>best_rate) or (rate==best_rate and g1==best_g1 and c1<best_c1):
            best_rate, best_c1, best_g1 = rate, c1, g1

        if progress:
            progress.update(incr)

    log.info('Best rate %f at (%.3f,%.3f)', best_rate, best_c1, best_g1)
    return best_rate, best_c1, best_g1


def svm_train(labels, vectors, model_file, progress=None):
    problem = svm.svm_problem(labels.tolist(), vectors.tolist(), isKernel=True)

    # find best C and γ parameters
    C_RANGE = C_BEGIN, C_END, COARSE_STEP
    G_RANGE = G_BEGIN, G_END, COARSE_STEP
    rate, c1, g1 = _grid_search(problem, C_RANGE, G_RANGE, progress)

    C_RANGE = c1-COARSE_STEP, c1+COARSE_STEP, FINE_STEP
    G_RANGE = g1-COARSE_STEP, g1+COARSE_STEP, FINE_STEP
    rate, c1, g1 = _grid_search(problem, C_RANGE, G_RANGE, progress,
                                rate, c1, g1)

    # Train the SVM model
    libc_srand(0) # reset random seed between runs
    params = svm.svm_parameter('-q -t 4 -b 1 -c %f -g %f' % (2.0**c1, 2.0**g1))
    model = svmutil.svm_train(problem, params)

    svmutil.svm_save_model(model_file, model)

