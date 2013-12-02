#!/usr/bin/python
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

from opendiamond.filter import Filter
from opendiamond.filter.parameters import StringParameter, NumberParameter, \
    BooleanParameter
from collections import namedtuple
from cStringIO import StringIO
import numpy as np
import pymorph
from PIL import Image

from .evaluate_classifier import load_from_classifier
from ..image import convert_to_cielab, convert_to_primaries
from ..forest import normalize
from ..svm_ import make_vectors, svm_predict

ALPHA = 0.5

ImageLabelTuple = namedtuple('ImageLabelTuple', ['image', 'label'])
ClassifierTuple = namedtuple('ClassifierTuple', ['forest0', 'hist0',
        'forest1', 'hist1', 'training_bosts', 'svmmodels', 'prior'])

def classify(classifier, image):
    rgb = np.asarray(image)
    Lab = convert_to_cielab(rgb)
    pcv = convert_to_primaries(rgb, None)
    image = ImageLabelTuple(np.dstack((Lab, rgb, pcv)), None)

    leafimage0 = classifier.forest0.compute_leafimage(image)

    if classifier.training_bosts is not None:
        bost = leafimage0.compute_bost()
        bost = normalize(bost).T
        vector = make_vectors(bost, classifier.training_bosts)
        svmresults = [0] + [ svm_predict(m, vector[0])
                             for m in classifier.svmmodels ]
        ILP = np.power(svmresults, ALPHA)
    else:
        #ILP = classifier.prior
        ILP = (1.,) * len(classifier.prior)

    dist0 = leafimage0.compute_distimage(classifier.hist0)
    dist0 = np.true_divide(dist0, dist0.sum(axis=2)[..., np.newaxis])

    iimage = ImageLabelTuple(dist0.cumsum(axis=0).cumsum(axis=1), None)
    leafimage1 = classifier.forest1.compute_leafimage(iimage, IS_INTEGRAL=True)
    dist1 = leafimage1.compute_distimage(classifier.hist1)
    dist1 = np.true_divide(dist1, dist1.sum(axis=2)[..., np.newaxis])

    result = dist0 * ILP * dist1
    return np.true_divide(result, result.sum(axis=2)[..., np.newaxis])

def analyze_blobs(heatmap, as_ratio=False):
    labeled = pymorph.label(heatmap)
    blobs = pymorph.blob(labeled, 'area', 'data')
    if len(blobs) == 0:
        return 0, 0, 0, 0, 0, 0, 0 

    stats = np.asarray([np.count_nonzero(heatmap), len(blobs), np.mean(blobs),
                np.median(blobs), np.std(blobs), min(blobs), max(blobs)])
    if as_ratio:
       stats = (stats * 100.0) / heatmap.size
    return stats

def analyze(result, size_ratio=False, test_distribution=False):
    if not test_distribution:
        stats = analyze_blobs(result, size_ratio)

    else:
        regions = ( region for split in np.array_split(result, 4, axis=0)
                           for region in np.array_split(split, 4, axis=1) )
        region_stats = np.vstack(analyze_blobs(region[1], size_ratio)
                                 for region in regions)
        stats = np.std(region_stats, axis=0)

    return {
        'coverage': stats[0],
        'blobs': stats[1],
        'mean': stats[2],
        'median': stats[3],
        'stddev': stats[4],
        'minimum': stats[5],
        'maximum': stats[6],
    }


class STFFilter(Filter):
    params = (
        NumberParameter("label_class"),
        BooleanParameter("resize"),
        BooleanParameter("argmax"),
        NumberParameter("threshold"),
    )
    blob_is_zip = True

    def __init__(self, *xargs, **kwargs):
        Filter.__init__(self, *xargs, **kwargs)

        self.label_class = int(self.label_class)
        self.classifier = ClassifierTuple(*load_from_classifier(self.blob))

    def __call__(self, obj):
        obj_image = obj.image.copy()
        if self.resize:
            obj_image.thumbnail((320, 320), Image.ANTIALIAS)

        result = classify(self.classifier, obj_image)

        if self.argmax:
            argmax = result.argmax(axis=2)
            mask = np.dstack(argmax == c for c in xrange(result.shape[2]))
            result = result * mask

        result = np.uint8(result * 255.0)

        if self.threshold:
            result = result >= self.threshold
            result = np.uint8(result * 255.0)

        if self.label_class:
            result = result[..., self.label_class]
            if not result.any():
                return False

            heatmap = Image.fromarray(result)
            obj.set_heatmap('stf.%s.png' % self.session.name, heatmap)
        else:
            for cls in xrange(1, result.shape[2]):
                heatmap = Image.fromarray(result[..., cls])
                obj.set_heatmap('stf.%s.%d.png' % (self.session.name, cls), heatmap)

        return True

class PostProcessFilter(Filter):
    params = (
        StringParameter("STF_name"),
        NumberParameter("threshold"),
        NumberParameter("close_size"),
        NumberParameter("open_size"),
        BooleanParameter("size_ratio"),
        BooleanParameter("distribution"),
        BooleanParameter("locate_blobs"),
    )

    def __init__(self, *xargs, **kwargs):
        Filter.__init__(self, *xargs, **kwargs)

        self.close_SE = pymorph.sedisk(int(self.close_size)) \
                        if self.close_size else None
        self.open_SE  = pymorph.sedisk(int(self.open_size)) \
                        if self.open_size else None

    def __call__(self, obj):
        result = obj.get_binary('stf.%s.png' % self.STF_name)
        result = np.asarray(Image.open(StringIO(result)))

        if self.threshold: # convert back to boolean values
            result = (result != 0)

        if self.close_SE is not None:
            result = pymorph.close(result, self.close_SE)
        if self.open_SE is not None:
            result = pymorph.open(result, self.open_SE)

        if not result.any():
            return False

        stats = analyze(result, self.size_ratio, self.distribution)
        for k, v in stats.items():
            obj['stf.%s.%s' % (self.session.name, k)] = v

        if self.locate_blobs:
            labeled = pymorph.label(result)
            bounds = pymorph.blob(labeled, 'boundingbox', 'data')

            scale = float(obj.image.size[0]) / result.shape[1]
            patches = [ ((c[0]*scale, c[1]*scale), (c[2]*scale, c[3]*scale))
                        for c in bounds ]

            obj.set_patches('_filter.%s.patches' % self.session.name,
                            0.0, patches)
        else:
            if self.threshold: # PIL can't import from boolean arrays
                result = np.uint8(result * 255.0)

            heatmap = Image.fromarray(result)
            if heatmap.size != obj.image.size:
                heatmap = heatmap.resize(obj.image.size, Image.ANTIALIAS)

            obj.set_heatmap('_filter.%s.heatmap.png' % self.session.name,
                            heatmap)
        return True

def main():
    Filter.run([STFFilter, PostProcessFilter])

if __name__ == '__main__':
    main()

