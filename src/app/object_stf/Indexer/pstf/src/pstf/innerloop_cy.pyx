# cython: profile=False
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

cimport cython
cimport numpy as np
import numpy as np
from itertools import izip

cdef enum FUNCTIONS:
    LEAF = 0
    VAL = 1
    ADD = 2
    SUB = 3
    ABS = 4
    BOX = 5

@cython.boundscheck(False)
@cython.wraparound(False)
def compute_leafimage(tree, img, mask0):
    cdef:
        np.ndarray[np.uint8_t, ndim=1] fn = tree.functions
        np.ndarray[np.int16_t, ndim=2] fv = tree.fvalues
        np.ndarray[np.float_t, ndim=1] th = tree.thresholds
        np.ndarray[np.float_t, ndim=3] im = img.image
        np.ndarray[np.int_t, ndim=2] indices
        np.ndarray[np.int_t, ndim=2] leafimage
        unsigned int f, i, NFUNCS=fn.shape[0]
        unsigned int BORDER = img.BORDER, STRIDE = img.STRIDE, l, r, t, b
        int x, y, xs, ys, x1, y1, c1, x2, y2, c2, result
        double tl, br, tr, bl, threshold

    assert fn.dtype == np.uint8 and fv.dtype == np.int16 and \
           th.dtype == np.float and im.dtype == np.float

    leafimage = np.empty_like(mask0, dtype='int')

    indices = np.argwhere(mask0)
    for y,x in indices:
        xs,ys = BORDER+x*STRIDE, BORDER+y*STRIDE
        i = 0
        while i < NFUNCS:
            f = fn[i]
            if f == LEAF: break

            x1 = fv[i,0]
            y1 = fv[i,1]
            c1 = fv[i,2]
            x2 = fv[i,3]
            y2 = fv[i,4]
            c2 = fv[i,5]
            threshold = th[i]

            l = xs+x1
            r = xs+x2
            t = ys+y1
            b = ys+y2
            tl = im[t,l,c1]
            br = im[b,r,c2]

            if f == VAL:   result = tl > threshold
            elif f == ADD: result = (tl + br) > threshold
            elif f == SUB: result = (tl - br) > threshold
            elif f == ABS:
                if tl > br: result = (tl - br) > threshold
                else:       result = (br - tl) > threshold
            elif f == BOX:
                tr = im[t,r,c1]
                bl = im[b,l,c1]
                result = ((tl + br) - (tr + bl)) > threshold
            else:
                result = 0

            if not result: i = 2*i + 1
            else:          i = 2*i + 2
        leafimage[y,x] = i+1
    return leafimage


@cython.final
cdef class DataPoint:
    cdef:
        readonly np.ndarray image
        readonly unsigned int label
        readonly unsigned int row, col

    def __init__(self, np.ndarray[np.float_t, ndim=3] image not None,
                 unsigned int row, unsigned int col, unsigned int label):
        self.image = image
        self.label = label
        self.row = row
        self.col = col

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef double item(self, int row, int col, int chan):
        cdef:
            np.ndarray[np.float_t, ndim=3] _image = self.image
            unsigned int rows = _image.shape[0] - 1
            unsigned int cols = _image.shape[1] - 1

        row += self.row
        if   row < 0:    row = 0
        elif row > rows: row = rows

        col += self.col
        if   col < 0:    col = 0
        elif col > cols: col = cols

        return _image[<unsigned int>row, <unsigned int>col, <unsigned int>chan]

    def __getitem__(self, tuple index):
        cdef int row, col, chan
        row, col, chan = index
        return self.item(row, col, chan)

cdef class Candidate:
    cdef:
        readonly FUNCTIONS function
        int x1,y1,c1,x2,y2,c2
        public double threshold

    def __init__(self, FUNCTIONS function, int x1, int y1, int c1,
                 int x2, int y2, int c2, double threshold=float('NaN')):
        self.function = function
        self.x1 = x1
        self.y1 = y1
        self.c1 = c1
        self.x2 = x2
        self.y2 = y2
        self.c2 = c2
        self.threshold = threshold

    property fvalues:
        def __get__(self):
            return (self.x1,self.y1,self.c1,self.x2,self.y2,self.c2)

    @cython.boundscheck(False)
    @cython.wraparound(False)
    cdef _vfunc(self, np.ndarray[object, ndim=1] datapoints,
                      np.ndarray[np.float_t, ndim=1] out):
        cdef:
            double tl,br,tr,bl
            unsigned int i
            DataPoint dp

        for i in xrange(datapoints.shape[0]):
            dp = datapoints[i]
            tl = dp.item(self.y1,self.x1,self.c1)
            br = dp.item(self.y2,self.x2,self.c2)

            if self.function == VAL:   out[i] = tl
            elif self.function == ADD: out[i] = tl + br
            elif self.function == SUB: out[i] = tl - br
            elif self.function == ABS:
                if tl > br: out[i] = tl - br
                else:       out[i] = br - tl
            else:
                assert(self.function == BOX)
                tr = dp.item(self.y1,self.x2,self.c1)
                bl = dp.item(self.y2,self.x1,self.c2)
                out[i] = (tl+br)-(tr+bl)
        return out

    def vfunc(self, np.ndarray[object, ndim=1] datapoints not None,
              np.ndarray[np.float_t, ndim=1] out=None):
        if out is None:
            out = np.empty_like(datapoints, dtype='float')
        return self._vfunc(datapoints, out)

