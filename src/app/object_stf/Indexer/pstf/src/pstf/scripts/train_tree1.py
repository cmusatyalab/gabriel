#!/usr/bin/python
#
# STF trainer, trains an STF predicate for the OpenDiamond platform
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

import argparse
import logging
import numpy as np
from cStringIO import StringIO
from zipfile import ZipFile

from .. import algum, image, trainer, utils
from ..forest import Forest, LeafImage, normalize
from ..utils import macheps, DIRICHLET

log = logging.getLogger('pstf.train_tree1')

forest1_args = argparse.ArgumentParser(add_help=False)
_args = forest1_args.add_argument_group("forest1 specific settings")
_args.add_argument('--forest1_trees', metavar='N', type=int,
                   help="number of trees in forest1")
_args.add_argument('--forest1_depth', metavar='N', type=int,
                   help="maximum depth of forest1 trees")
_args.add_argument('--forest1_box_size', metavar='N', type=int,
                   help="area searched for candidate regions")
_args.add_argument('--forest1_box_rows', metavar='N', type=int,
                   help="maximum height of a candidate region")
_args.add_argument('--forest1_box_cols', metavar='N', type=int,
                   help="maximum width of a candidate region")
_args.add_argument('--forest1_features', metavar='N', type=int,
                   help="number of evaluated candidate function")
_args.add_argument('--forest1_thresholds', metavar='N', type=int,
                   help="number of evaluated threshold values")
_args.add_argument('--forest1_training_sample_frequency', metavar='N', type=int,
                   help="stride over datapoints during training")
_args.add_argument('--forest1_filling_sample_frequency', metavar='N', type=int,
                   help="stride over datapoints when computing histograms")
_args.add_argument('--forest1_image_probability', metavar='P', type=float,
                   help="probability an image is used for training")
_args.add_argument('--forest1_datapoint_probability', metavar='P', type=float,
                   help="probability a datapoint is used for training")

_arg_weight_choices = ('linear', 'logarithmic', 'none')
_args.add_argument('--forest1_training_weights', choices=_arg_weight_choices,
                   help="Weighing used for evaluating datapoint importance")
_args.set_defaults(
    forest1_trees=6,
    forest1_depth=14,
    forest1_box_size=160,
    forest1_box_rows=100,
    forest1_box_cols=100,
    forest1_features=400,
    forest1_thresholds=5,
    forest1_training_sample_frequency=8,
    forest1_filling_sample_frequency=1,
    forest1_image_probability=0.5,
    forest1_datapoint_probability=0.5,
    forest1_training_weights='linear',
)

def load_forest_from_classifier(classifier, name):
    f = StringIO(classifier.read(name))
    forest = Forest.load(f)
    return forest

# load numpy array from zip archive
def load_ndarray(archive, name):
    f = StringIO(archive.read(name))
    array = np.load(f)
    return array

class DistImage(image.LabeledImage):
    def __init__(self, labeledimage, forest0, hist0):
        super(DistImage, self).__init__(labeledimage)
        self.forest0 = forest0
        self.hist0 = hist0

    def get_image(self):
        leafimage = self._image
        if not isinstance(leafimage, LeafImage):
            leafimage = self.forest0.compute_leafimage(leafimage)

        distimg = leafimage.compute_distimage(self.hist0)
        # normalize
        distimg = np.true_divide(distimg,
                                 distimg.sum(axis=2)[...,np.newaxis]+macheps)
        # compute integral image
        return distimg.cumsum(axis=0).cumsum(axis=1)

    def get_label(self):
        return self._image.label

###
### Train a tree for STF forest1
###
def train_tree1(seed, args):
    # seed random number generator(s)
    utils.random_seed(seed)

    log.info("loading and converting training images")
    trainingset, classes = algum.load_dataset(args.trainingset,
            transforms=args.transforms, unlabeled=args.unlabeled,
            versus=args.versus, remap=args.remap, resize=args.resize)
    NCLASSES = len(classes)

    # compute inverse frequency of occurrence (training weight)
    log.info("computing training weights")
    label_freqs = np.zeros((NCLASSES,))
    for img in trainingset:
        label_freqs += np.bincount(img.label.ravel(), minlength=NCLASSES)
    _dirichlet = DIRICHLET / NCLASSES
    label_freqs[0] = 0
    inverse_freqs = (label_freqs.sum()+DIRICHLET) / (label_freqs+_dirichlet)

    if args.forest1_training_weights == 'none':
        training_weights = np.ones((NCLASSES,), dtype='float')
    elif args.forest1_training_weights == 'linear':
        training_weights = inverse_freqs
    else: # 'logarithmic'
        training_weights = np.log(inverse_freqs)
    inverse_freqs[0] = 0

    log.info("loading forest0 and histograms")
    classifier = ZipFile(args.classifier)
    forest0 = load_forest_from_classifier(classifier, 'forest0.npz')
    hist0 = load_ndarray(classifier, "hist0.npy")
    hist0 = np.insert(normalize(hist0), 0, 0, axis=0)
    classifier.close()

    log.info("computing image histograms")
    distset = [ DistImage(img, forest0, hist0) for img in trainingset ]
    del trainingset

    log.info("sampling data")
    data = utils.sample_data_points(distset,
                                    args.forest1_training_sample_frequency,
                                    args.forest1_image_probability,
                                    args.forest1_datapoint_probability)
    del distset

    MAX_CLASSES = data[0].image.shape[2]
    candidate = trainer.Forest1Candidate(args.forest1_box_size,
                                         args.forest1_box_rows,
                                         args.forest1_box_cols,
                                         args.forest1_training_sample_frequency,
                                         MAX_CLASSES)

    log.info("training forest1 tree %d" % hash(seed))
    tree = trainer.train_forest(data, training_weights, candidate,
                                args.forest1_features,
                                args.forest1_thresholds,
                                1, args.forest1_depth)
    return tree

def main():
    parser = utils.ArgumentParser(description="Train a tree for STF forest1",
                                  parents=[utils.stf_common_args,forest1_args],
                                  fromfile_prefix_chars='@',
                        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('--seed', type=int, help="random seed")
    parser.add_argument('classifier', default="stf_classifier.pred",
                        help="classifier containing STF forest0")
    parser.add_argument('output', nargs='?', default="tree1.npz",
                        help="output tree file")

    args = parser.parse_args()
    utils.log_args(args)

    tree = train_tree1(args.seed, args)
    tree.save(args.output)

if __name__ == '__main__':
    main()

