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

# we still depend on numpy to give us the data so we normally shouldn't
# ever use this code. That's ok though, as it is incredibly slow.
from collections import namedtuple
import numpy as np
from .utils import FUNCTIONS

LEAF = FUNCTIONS.index('leaf')
VAL = FUNCTIONS.index('val')
ADD = FUNCTIONS.index('add')
SUB = FUNCTIONS.index('sub')
ABS = FUNCTIONS.index('abs')
BOX = FUNCTIONS.index('box')

def compute_leafimage(tree, img, mask0):
    leafimage = np.zeros_like(mask0, dtype='int')

    _fn = tree.functions.item
    _fv = tree.fvalues.item
    _th = tree.thresholds.item
    _im = img.image.item
    BORDER, STRIDE = img.BORDER, img.STRIDE
    NFUNCS = tree.functions.shape[0]

    for y, x in np.argwhere(mask0):
        xs, ys = BORDER+x*STRIDE, BORDER+y*STRIDE
        i = 0
        while i < NFUNCS:
            f = _fn(i)
            if f == LEAF:
                break

            x1, y1, c1 = _fv((i, 0)), _fv((i, 1)), _fv((i, 2))
            x2, y2, c2 = _fv((i, 3)), _fv((i, 4)), _fv((i, 5))
            threshold = _th(i)

            l, r, t, b = xs+x1, xs+x2, ys+y1, ys+y2
            tl, br = _im((t, l, c1)), _im((b, r, c2))

            if f == LEAF:
                result = 0

            elif f == VAL:
                result = tl > threshold

            elif f == ADD:
                result = (tl + br) > threshold

            elif f == SUB:
                result = (tl - br) > threshold

            elif f == ABS:
                if tl > br:
                    result = (tl - br) > threshold
                else:
                    result = (br - tl) > threshold

            elif f == BOX:
                #assert(c1 == c2)
                tr, bl = _im((t, r, c1)), _im((b, l, c1))
                result = ((tl + br) - (tr + bl)) > threshold

            else:
                raise AssertionError, "unknown function"

            i = 2*i + 1 + result
        leafimage[y, x] = i+1
    return leafimage

Index = namedtuple('Index', ['row', 'col', 'chan'])

class DataPoint(object):
    def __init__(self, image, row, col, label):
        self.image = image
        self.label = label
        self.row = row
        self.col = col
        rows, cols, chans = image.shape
        self.rows = rows - 1
        self.cols = cols - 1

    def __getitem__(self, index):
        row, col, chan = index

        row += self.row
        rows = self.rows
        if row < 0:
            row = 0
        elif row > rows:
            row = rows

        col += self.col
        cols = self.cols
        if col < 0:
            col = 0
        elif col > cols:
            col = cols

        return self.image.item(row, col, chan)

def ufunc_val(dp, x1, y1, c1):
    return dp[y1, x1, c1]
vfunc_val = np.vectorize(ufunc_val, otypes=[np.float])

def ufunc_add(dp, x1, y1, c1, x2, y2, c2):
    return dp[y1, x1, c1] + dp[y2, x2, c2]
vfunc_add = np.vectorize(ufunc_add, otypes=[np.float])

def ufunc_sub(dp, x1, y1, c1, x2, y2, c2):
    return dp[y1, x1, c1] - dp[y2, x2, c2]
vfunc_sub = np.vectorize(ufunc_sub, otypes=[np.float])

def ufunc_abs(dp, x1, y1, c1, x2, y2, c2):
    v = dp[y1, x1, c1] - dp[y2, x2, c2]
    if v < 0: return -v
    else:     return v
vfunc_abs = np.vectorize(ufunc_abs, otypes=[np.float])

def ufunc_box(dp, x1, y1, c1, x2, y2, c2):
    tl, br = dp[y1, x1, c1], dp[y2, x2, c2]
    tr, bl = dp[y1, x2, c1], dp[y2, x1, c2]
    return (tl+br)-(tr+bl)
vfunc_box = np.vectorize(ufunc_box, otypes=[np.float])


class Candidate(object):
    def __init__(self, function, x1, y1, c1, x2, y2, c2,
                 threshold=float('NaN')):
        self.function = function
        self.fvalues = (x1, y1, c1, x2, y2, c2)
        self.threshold = threshold

    def vfunc(self, datapoints, out=None):
        function = self.function
        x1, y1, c1, x2, y2, c2 = self.fvalues

        if out is None:
            out = np.empty_like(datapoints, dtype='float')

        if function == VAL:
            out[:] = vfunc_val(datapoints, x1, y1, c1)
        elif function == ADD:
            out[:] = vfunc_add(datapoints, x1, y1, c1, x2, y2, c2)
        elif function == SUB:
            out[:] = vfunc_sub(datapoints, x1, y1, c1, x2, y2, c2)
        elif function == ABS:
            out[:] = vfunc_abs(datapoints, x1, y1, c1, x2, y2, c2)
        else:
            assert(function == BOX)
            out[:] = vfunc_box(datapoints, x1, y1, c1, x2, y2, c2)
        return out

