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

## Collection of various helper, wrapper and utility functions

##
## ordered list of operator names in STF trees
##
FUNCTIONS = ['leaf', 'val', 'add', 'sub', 'abs', 'box']

##
## Machine epsilon
##
## Upper bound on the relative error due to rounding in floating point math
## (actual value depends on the precision of the floating point number, for
## doubles it is pow(2, -53))
## We use this to add into divisors to avoid divide-by-zero.
##
macheps = 1.e-15
assert 1.0+macheps > 1.0

##
## Dirichlet
##
## A statistical 'trick' that avoids divide by zero cases, the idea is that
## even if we observe no instances of a particular event, there is still a
## non-zero chance they exist.
##
DIRICHLET = 0.0001


##
## Initialize pseudo random number sequences with a known seed value.
##
import ctypes
import random
# to reset rand() between validation runs.
libc_srand = ctypes.CDLL("libc.so.6").srand
def random_seed(seed):
    if seed is None:
        seed = int(random.random() * (1<<32))
    libc_srand(hash(seed))
    random.seed(seed)

##
## Implement sample_with_replacement as a generator function.
##
def sample_with_replacement(seq, k):
    return ( random.choice(seq) for _ in xrange(k) )

##
## sample a list of data points from a trainingset
##
import math
import numpy as np
from .innerloop import DataPoint
def sample_data_points(trainingset, training_sample_frequency,
                       image_probability, datapoint_probability):
    if image_probability != 1:
        trainingset = list(trainingset)
        N = int(math.ceil(len(trainingset) * image_probability))
        trainingset = sample_with_replacement(trainingset, N)

    STRIDE = training_sample_frequency
    data = []
    for labeledimage in trainingset:
        label, image = labeledimage.label, labeledimage.image
        labeledimage.drop_caches()

        mask = np.zeros_like(label, dtype='bool')
        mask[::STRIDE, ::STRIDE] = True
        datapoints = np.argwhere(label * mask)

        N = int(math.ceil(len(datapoints) * datapoint_probability))

        for y, x in sample_with_replacement(datapoints, N):
            cls = label.item((y, x))
            dp = DataPoint(image, y, x, cls)
            data.append(dp)
    return data


##
## Memoizer to speed up some SVM internals
## 
def memoize(f):
    cache = {}
    def inner(*args):
        key = hash(args)
        if key not in cache:
            cache[key] = f(*args)
        return cache[key]
    return inner

##
## Somewhat similar to memoization avoids the cache closure and relies
## on the instance dict of a class object
##     http://blog.pythonisito.com/2008/08/lazy-descriptors.html
##
class LazyProperty(object):
    def __init__(self, func):
        self._func = func
        self.__name__ = func.__name__
        self.__doc__ = func.__doc__

    def __get__(self, obj, klass=None):
        if obj is None:
            return None
        result = obj.__dict__[self.__name__] = self._func(obj)
        return result

##
## Progress logging
## 
import logging
import time

# Add special 'PROGRESS' log level
PROGRESS = 35
logging.addLevelName(PROGRESS, 'PROGRESS')

### Helper to report status back to Algum.
### Uses special characters to mark them in the output stream.
progress_log = logging.getLogger('pstf.progress')
class Progress(object):
    """ Progress logging

    If incremental is True, we only display incremental updates and let Algum
    handle the accumulation. This allows multiple concurrent worker threads to
    independently report progress towards the completion of a larger task.
    """
    def __init__(self, message=None, total_units=1, incremental=False):
        self.total_units = total_units
        self.incremental = incremental
        self.processed = 0
        self.reported = 0.
        self.refreshed = 0
        self.avg_rate = 0
        if message:
            self.status(message)
        self.started = self.updated = time.time()
        self.set(0)

    def update(self, units):
        count = self.processed + units
        now = self._update_processed(count)
        if (now - self.refreshed) > 2:
            self._refresh()

    def set(self, units):
        self._update_processed(units)
        self._refresh()

    def _update_processed(self, count):
        count = min(count, self.total_units)
        now = time.time()
        deltaN = count - self.processed
        deltaT = now - self.updated
        if deltaN < 0:
            self.avg_rate = 0
        elif deltaT:
            rate = deltaN / deltaT
            self.avg_rate = (0.5*rate + 0.5*self.avg_rate)
        self.processed = count
        self.updated = now
        return now

    def status(self, message):
        # \u4DFF status message
        progress_log.log(PROGRESS, "䷿  %s", message)

    def _refresh(self):
        if not progress_log.isEnabledFor(PROGRESS):
            return

        #pct_done = 100.0*self.processed/self.total_units
        remaining = self.total_units - self.processed
        if self.avg_rate:
            ETA = int(self.updated + (remaining / self.avg_rate))
        else:
            ETA = 0
        ETA = ETA and time.strftime("%T", time.localtime(ETA)) or "--:--:--"
        # \u4DE2 units completed, total units, average rate, ETA
        if not self.incremental:
            progress_log.log(PROGRESS, "䷢  %f %f %f %s", self.processed,
                             self.total_units, self.avg_rate, str(ETA))
        else:
            processed = float(self.processed) / self.total_units
            delta_str = "%f" % (processed - self.reported)
            increment = float(delta_str)
            if increment:
                # \u03B9, increment, percent done for this worker, ETA
                progress_log.log(PROGRESS, "ι %s %.1f %s",
                                 delta_str, processed*100., str(ETA))
                self.reported += increment
        self.refreshed = self.updated

    def done(self):
        now = time.time()
        dT = now - self.started
        self.set(self.total_units)
        ETA = int(now)
        # \u4DFE time elapsed, units per second, time finished
        ETA = time.strftime("%T", time.localtime(ETA))
        progress_log.log(PROGRESS, "䷾  %f %f %s",
                         dT, self.total_units/dT, str(ETA))

##
## argument parsers for classifier and tree trainers
##
import argparse
import json
import yaml

def serialize_args(args):
    args = vars(args).copy()
    args['classifier'] = 'stf_classifier.pred'
    args['remap'] = json.dumps(args['remap'])
    return yaml.safe_dump(args, default_flow_style=False)

class ArgumentParser(argparse.ArgumentParser):
    def convert_arg_line_to_args(self, arg_line):
        args = yaml.safe_load(arg_line)
        if args is not None:
            if args.has_key('remap'):
                args['remap'] = json.loads(args['remap'])
            self.set_defaults(**args)
        return []

    def parse_known_args(self, *args, **kwargs):
        # defaults are assigned to namespace object before we start parsing,
        # so we have to parse twice to get the right defaults
        super(ArgumentParser, self).parse_known_args(*args, **kwargs)
        return super(ArgumentParser, self).parse_known_args(*args, **kwargs)

stf_common_args = argparse.ArgumentParser(add_help=False)
stf_common_args.add_argument('-v', '--verbose',
                             action='store_const', dest='loglevel',
                             default=logging.WARNING, const=logging.INFO,
                             help="enable verbose logging")
stf_common_args.add_argument('-d', '--debug', action='store_true',
                             help="enable debugging")
stf_common_args.add_argument('trainingset', help="trainingset zip archive")

_args = stf_common_args.add_argument_group("input specific settings")
_args.add_argument('--resize', action='store_true',
                   help="scale inputs down to 320x320, preserving aspect")

_arg_transform_choices = ('all', 'hflip', 'none')
_args.add_argument('--transforms', choices=_arg_transform_choices,
                   default='all', help="transformations to apply")

_args.add_argument('--remap', action='append', nargs=2, default=[],
                   help="specify label to class remapping")
_args.add_argument('--unlabeled', metavar="CLASS", type=int, default=0,
                   help="default value for unlabeled datapoints")
_args.add_argument('--versus', metavar="CLASS", type=int, default=0,
                   help="select class for binary class vs. not-class trainer")

##
## log currently active settings, also initializes basic logging
##
ulog = logging.getLogger('pstf.utils')
def log_args(args):
    if args.debug:
        args.loglevel = logging.DEBUG
    logging.basicConfig(level=args.loglevel, format="%(asctime)s - %(message)s")
    ulog.info("[Settings]\n%s", serialize_args(args))

