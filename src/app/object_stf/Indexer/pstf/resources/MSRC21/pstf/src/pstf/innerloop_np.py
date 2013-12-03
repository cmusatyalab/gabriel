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

import numpy as np
from .utils import FUNCTIONS

LEAF = FUNCTIONS.index('leaf')
VAL = FUNCTIONS.index('val')
ADD = FUNCTIONS.index('add')
SUB = FUNCTIONS.index('sub')
ABS = FUNCTIONS.index('abs')
BOX = FUNCTIONS.index('box')

def compute_leafimage(tree, img, mask0):
    leafimage = np.ones(mask0.shape, dtype='int') * mask0
    results = np.empty(mask0.shape, dtype='bool')
    tmp = np.empty(mask0.shape, dtype='float')

    _fn = tree.functions.item
    _fv = tree.fvalues.item
    _th = tree.thresholds.item
    NFUNCS = tree.functions.shape[0]

    for i in xrange(NFUNCS):
        f = _fn(i)
        if f == LEAF:
            continue

        mask = np.equal(leafimage, i+1)
        if not np.any(mask):
            continue

        x1, y1, c1 = _fv((i, 0)), _fv((i, 1)), _fv((i, 2))
        x2, y2, c2 = _fv((i, 3)), _fv((i, 4)), _fv((i, 5))
        threshold = _th(i)

        tl, br = img.slice(x1, y1, c1), img.slice(x2, y2, c2)
        if f == LEAF:
            results.fill(False)
        elif f == VAL:
            # results = tl > t
            np.greater(tl, threshold, out=results)
        elif f == ADD:
            # results = (tl + br) > t
            np.add(tl, br, out=tmp)
            np.greater(tmp, threshold, out=results)
        elif f == SUB:
            # results = (tl - br) > t
            np.subtract(tl, br, out=tmp)
            np.greater(tmp, threshold, out=results)
        elif f == ABS:
            # results = abs(tl - br) > t
            np.subtract(tl, br, out=tmp)
            np.absolute(tmp, out=tmp)
            np.greater(tmp, threshold, out=results)
        elif f == BOX:
            #assert(c1 == c2)
            tr, bl = img.slice(x2, y1, c1), img.slice(x1, y2, c1)
            # results = (tl + br) - (tr + bl) > t
            np.add(tl, br, out=tmp)
            np.subtract(tmp, tr, out=tmp)
            np.subtract(tmp, bl, out=tmp)
            np.greater(tmp, threshold, out=results)
        else: raise AssertionError, "unknown function"

        # results = (results <= threshold)
        # leafimage = leafimage             if not mask
        # leafimage = leafimage * 2         if mask and result
        # leafimage = leafimage * 2 + 1     if mask and not result
        np.logical_and(mask, results, out=results)
        np.multiply(leafimage, mask+1, out=leafimage)
        np.add(leafimage, results, out=leafimage)
    return leafimage

