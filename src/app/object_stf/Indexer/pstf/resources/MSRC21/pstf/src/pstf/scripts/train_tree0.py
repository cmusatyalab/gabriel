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

from .. import algum, trainer, utils
from ..utils import DIRICHLET

log = logging.getLogger('pstf.train_tree0')

forest0_args = argparse.ArgumentParser(add_help=False)
_args = forest0_args.add_argument_group("forest0 specific settings")
_args.add_argument('--forest0_trees', metavar='N', type=int,
                   help="number of trees in forest0")
_args.add_argument('--forest0_depth', metavar='N', type=int,
                   help="maximum depth of forest0 trees")
_args.add_argument('--forest0_box_size', metavar='N', type=int,
                   help="size of candidate region around a datapoint")
_args.add_argument('--forest0_features', metavar='N', type=int,
                   help="number of evaluated candidate function")
_args.add_argument('--forest0_thresholds', metavar='N', type=int,
                   help="number of evaluated threshold values")
_args.add_argument('--forest0_training_sample_frequency', metavar='N', type=int,
                   help="stride over datapoints during training")
_args.add_argument('--forest0_filling_sample_frequency', metavar='N', type=int,
                   help="stride over datapoints when computing histograms")
_args.add_argument('--forest0_image_probability', metavar='P', type=float,
                   help="probability an image is used")
_args.add_argument('--forest0_datapoint_probability', metavar='P', type=float,
                   help="probability a datapoint is used")

_arg_weight_choices = ('linear', 'logarithmic', 'none')
_args.add_argument('--forest0_training_weights', choices=_arg_weight_choices,
                   help="Weighing used for evaluating datapoint importance")
_args.set_defaults(
    forest0_trees=6,
    forest0_depth=10,
    forest0_box_size=15,
    forest0_features=400,
    forest0_thresholds=5,
    forest0_training_sample_frequency=8,
    forest0_filling_sample_frequency=1,
    forest0_image_probability=1.0,
    forest0_datapoint_probability=0.25,
    forest0_training_weights='linear',
)

###
### Train a tree for STF forest0
###
def train_tree0(seed, args):
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
    for image in trainingset:
        label_freqs += np.bincount(image.label.ravel(), minlength=NCLASSES)
    _dirichlet = DIRICHLET / NCLASSES
    label_freqs[0] = 0
    inverse_freqs = (label_freqs.sum()+DIRICHLET) / (label_freqs+_dirichlet)

    if args.forest0_training_weights == 'none':
        training_weights = np.ones((NCLASSES,), dtype='float')
    elif args.forest0_training_weights == 'linear':
        training_weights = inverse_freqs
    else: # 'logarithmic'
        training_weights = np.log(inverse_freqs)
    inverse_freqs[0] = 0

    log.info("sampling data")
    data = utils.sample_data_points(trainingset,
                                    args.forest0_training_sample_frequency,
                                    args.forest0_image_probability,
                                    args.forest0_datapoint_probability)

    MAX_COLOR_CHANNELS = data[0].image.shape[2]
    candidate = trainer.Forest0Candidate(args.forest0_box_size,
                                         MAX_COLOR_CHANNELS)

    log.info("training forest0 tree %d" % hash(seed))
    tree = trainer.train_forest(data, training_weights, candidate,
                                args.forest0_features,
                                args.forest0_thresholds,
                                1, args.forest0_depth)
    return tree

def main():
    parser = utils.ArgumentParser(description="Train a tree for STF forest0",
                                  parents=[utils.stf_common_args,forest0_args],
                                  fromfile_prefix_chars='@',
                      formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('--seed', type=int, help="random seed")
    parser.add_argument('classifier', default="stf_classifier.pred",
                        help="classifier (not yet used)")
    parser.add_argument('output', nargs='?', default="tree0.npz",
                        help="output tree file")

    args = parser.parse_args()
    utils.log_args(args)

    tree = train_tree0(args.seed, args)
    tree.save(args.output)

if __name__ == '__main__':
    main()

