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
import itertools
import json
import numpy as np
import pymorph
import sys
import tempfile
import zipfile

from ..algum import load_dataset
from ..forest import normalize
from ..image import make_palette, output_colormap, LabeledImage
from ..svm_ import load_model, make_vectors, svm_predict
from ..utils import macheps
from .train_tree1 import load_forest_from_classifier, load_ndarray, DistImage

STRIDE0 = 1
STRIDE1 = 1
ALPHA = 0.5

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

def cm_head(classes, CSV=True):
    if CSV:
        out = ["trees"]
        for fr, to in itertools.product(classes, classes[1:]):
            out.append("%s > %s" % (fr[0], to[0]))
    else:
        out = [" "*10]
        for color in classes[1:]:
            out.append("%10s" % color)
    return ",".join(out)

def cm_body(classes, image, reference, CSV=None):
    bins = []
    NCLASSES = len(classes)
    for idx in range(NCLASSES):
        masked_results = image * (reference==idx)
        counts = np.bincount(masked_results.ravel(),
                             minlength=NCLASSES)[1:]
        bins.append(counts)
    confusion_matrix = np.asarray(bins)

    if CSV is not None:
        return ("%d," % CSV) + ",".join(map(str, confusion_matrix.ravel()))

    out = []
    for idx, color in enumerate(classes):
        counts = confusion_matrix[idx]
        row = ["%10s" % color]
        for value in counts:
            row.append("%10d" % value)
        row.append("%10d" % counts.sum())
        out.append(",".join(row))

    sums = confusion_matrix.sum(axis=0)
    row = [" "*10] + [ "%10d" % value for value in sums ]
    out.append(",".join(row))

    true_positives = np.diag(confusion_matrix[1:])
    returned = confusion_matrix.sum(axis=0)
    existing = confusion_matrix.sum(axis=1)[1:]
    precision = np.true_divide(true_positives, returned + macheps)
    recall = np.true_divide(true_positives, existing + macheps)
    fmeasure = 2 * ((precision * recall) / (precision + recall + macheps))

    out.append("")
    out.append(",".join([" precision"] + ["%10f" % val for val in precision]))
    out.append(",".join(["    recall"] + ["%10f" % val for val in recall]))
    out.append(",".join([" f-measure"] + ["%10f" % val for val in fmeasure]))

    return "\n".join(out)

class Tee(object):
    def __init__(self, *fds):
        self.fds = fds

    def __del__(self):
        self.close()

    def close(self):
        for fd in self.fds:
            if fd not in [ sys.__stdout__, sys.__stderr__ ]:
                fd.close()
        self.fds = []

    def __getattr__(self, name):
        def f(*args, **kwargs):
            for fd in self.fds:
                getattr(fd, name)(*args, **kwargs)
        return f

def analyze_blobs(cls):
    labeled = pymorph.label(cls)
    blobs = pymorph.blob(labeled, 'area', 'data')
    return [ cls.sum(), len(blobs), np.mean(blobs), np.median(blobs),
             np.std(blobs), max(blobs), min(blobs) ]

def main():
    import argparse
    import logging
    import os
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument('classifier')
    parser.add_argument('--postprocess', action="store_true",
                        help='Run postprocessing, close blobs and remove noise')
    parser.add_argument('--vary-trees', action="store_true",
                        help='Test varying number of trees')
    parser.add_argument('dataset', help='Algum formatted trainingset or image')
    parser.add_argument('label', nargs='?', help='label if dataset is an image')
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s - %(message)s")

    classifier = zipfile.ZipFile(args.classifier)
    forest0, hist0, forest1, hist1, training_bosts, svmmodels, prior = \
        load_from_classifier(classifier)
    config = yaml.safe_load(classifier.read('config.txt'))
    unlabeled = config.get('unlabeled', 0)
    versus = config.get('versus', 0)
    remap = json.loads(config.get('remap', '[]'))
    classifier.close()

    if zipfile.is_zipfile(args.dataset):
        dataset, classes = load_dataset(args.dataset, transforms='none',
                                        unlabeled=unlabeled, versus=versus,
                                        remap=remap)
    else:
        Image.open(args.dataset) # test if we can actually open the image
        labels = [('unlabeled', ImageColor.getrgb('#000')),
                  ('red', ImageColor.getrgb('#f00')),
                  ('green', ImageColor.getrgb('#0f0')),
                  ('blue', ImageColor.getrgb('#00f'))]
        classes = [ label for label, _ in labels ]
        palette = make_palette(labels, classes, unlabeled, versus)
        dataset = [ LabeledImage(args.dataset, args.label, palette=palette) ]

    for image in dataset:
        name = os.path.splitext(image.name)[0]

        if image.label is not None:
            sys.stdout = Tee(sys.stdout, open("%s.txt" % name, 'w'))

        print "Classifying/segmenting", name
        leafimage0 = forest0.compute_leafimage(image, STRIDE0)

        print "Computing forest0 segmentation"
        dist0 = leafimage0.compute_distimage(hist0)
        dist0 = np.true_divide(dist0, dist0.sum(axis=2)[..., np.newaxis])

        result = dist0
        resultimage = Image.fromarray(np.uint8(result[..., 1:4]*255))
        resultimage.save('%s-forest0.png' % name)

        result = dist0.argmax(axis=2)
        resultimage = Image.fromarray(np.uint8(result), mode='P')
        resultimage.putpalette(output_colormap)
        resultimage.save('%s-argmax0.png' % name)

        if training_bosts is not None:
            print "Computing SVM classification"
            bost = leafimage0.compute_bost()
            bost = normalize(bost).T
            vector = make_vectors(bost, training_bosts)
            svmresults = [0] + [ svm_predict(m, vector[0])
                                 for m in svmmodels ]
            ILP = np.power(svmresults, ALPHA)
        else:
            #ILP = prior
            ILP = (1.,) * len(prior)

        print "Computing forest1 segmentation"
        di = DistImage(leafimage0, forest0, hist0)
        di.image = dist0.cumsum(axis=0).cumsum(axis=1)
        leafimage1 = forest1.compute_leafimage(di, STRIDE1, IS_INTEGRAL=True)

        dist1 = leafimage1.compute_distimage(hist1)
        dist1 = np.true_divide(dist1, dist1.sum(axis=2)[..., np.newaxis])

        result = dist0 * ILP * dist1
        result = np.true_divide(result, result.sum(axis=2)[..., np.newaxis])

        resultimage = Image.fromarray(np.uint8(result[..., 1:4]*255))
        resultimage.save('%s-colors.png' % name)

        result = result.argmax(axis=2)
        resultimage = Image.fromarray(np.uint8(result), mode='P')
        resultimage.putpalette(output_colormap)
        resultimage.save('%s-argmax.png' % name)

        if args.postprocess:
            print "Filling holes and removing noise"
            cSE = pymorph.sedisk(2)
            oSE = pymorph.sedisk(7)

            NCLASSES = result.max()
            cls = np.dstack(pymorph.open(pymorph.close(result == i+1, cSE), oSE)
                            for i in range(NCLASSES))

            nr, ng, nb = map(float, cls.reshape(-1, NCLASSES).sum(axis=0))
            nt = float(result.shape[0] * result.shape[1])

            stats = analyze_blobs(cls[1])

            regions = ( region for split in np.array_split(cls, 4, axis=0)
                               for region in np.array_split(split, 4, axis=1) )

            rstats = np.vstack(analyze_blobs(region[1]) for region in regions)

            print "%s, " % name, ", ".join(str(x) for x in [
                 ng/nt, ng/nr, nb/nt ] + stats + \
                 np.std(rstats, axis=0).tolist())

            # somehow combine the separate layers back into a single image
            resultimage = Image.fromarray(np.uint8(cls*255))
            resultimage.save('%s-afinal.png' % name)
            result = cls.argmax(axis=2) + 1

        if image.label is not None:
            print "STF confusion matrix"
            print cm_head(classes, CSV=False)
            print cm_body(classes, result, image.label)
            print

        if image.label is not None and args.vary_trees:
            print cm_head(classes)
            print "forest0 segmentation"
            imgs = [ hist0[leafimage0.image[i], i]
                     for i in xrange(leafimage0.TREE_COUNT) ]
            for i in xrange(leafimage0.TREE_COUNT):
                distimage0 = np.sum(imgs[:i+1], axis=0)
                print cm_body(classes, distimage0.argmax(axis=2), image.label, i+1)
            print

            print cm_head(classes)
            print "STF segmentation (x forest0 trees N forest1 trees)"
            for i in xrange(leafimage0.TREE_COUNT):
                dist0 = np.sum(imgs[:i+1], axis=0)
                # normalize
                dist0 = np.true_divide(dist0,
                                       dist0.sum(axis=2)[..., np.newaxis])
                di = DistImage(leafimage0, forest0, hist0)
                di.image = dist0.cumsum(axis=0).cumsum(axis=1)

                leafimage1 = forest1.compute_leafimage(di, STRIDE1,
                                                       IS_INTEGRAL=True)
                dist1 = leafimage1.compute_distimage(hist1)
                dist1 = np.true_divide(dist1,
                                       dist1.sum(axis=2)[..., np.newaxis])
                distimage = dist0 * ILP * dist1

                print cm_body(classes, distimage.argmax(axis=2), image.label, i+1)
            print

            print cm_head(classes)
            print "STF segmentation (N forest0 trees x forest1 trees)"
            imgs = [ hist1[leafimage1.image[i], i]
                     for i in xrange(leafimage1.TREE_COUNT) ]
            for i in xrange(leafimage1.TREE_COUNT):
                dist1 = np.sum(imgs[:i+1], axis=0)
                dist1 = np.true_divide(dist1,
                                       dist1.sum(axis=2)[..., np.newaxis])
                distimage = dist0 * ILP * dist1

                print cm_body(classes, distimage.argmax(axis=2), image.label, i+1)
            print

        image.drop_caches()
        sys.stdout = sys.__stdout__
        print

if __name__ == '__main__':
    main()

