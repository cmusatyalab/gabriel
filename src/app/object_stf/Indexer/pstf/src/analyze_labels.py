# -*- coding: utf-8 -*-
import sys
import argparse
import math
import numpy as np
import pstf
import pymorph

parser = argparse.ArgumentParser()
parser.add_argument('--stats', action='store_true',
                    help="dump  statistics for all features")
parser.add_argument('--features', default='0',
                    help="features to include (--features=1,2,3)")
parser.add_argument('dataset')
args = parser.parse_args()

def stats(sizes):
    N = len(sizes)
    mean = int(np.mean(sizes)) if N else 0
    median = int(np.median(sizes)) if N else 0
    maxsize = max(sizes) if N else 0
    minsize = min(sizes) if N else 0
    print ', '.join(map(str, [N, mean, median, maxsize, minsize]))

def dump_all_the_stats(dataset):
    for image in dataset:
        print 'imagename, color, metric, N, mean, median, max, min'
        for i, color in enumerate(classes):
            if i == 0: continue
            label = (image.label == i)
            items = pymorph.label(label)

            print "%s, %-5s, area,  " % (image.name, color),
            areas = pymorph.blob(items, 'area', 'data')
            stats(areas)

            print "%s, %-5s,2√(a/π)," % (image.name, color),
            stats([int(2*math.sqrt(a/math.pi)) for a in areas])

            print "%s, %-5s, width, " % (image.name, color),
            bounds = pymorph.blob(items, 'boundingbox', 'data')
            stats([xmax-xmin for xmin,ymin,xmax,ymax in bounds])

            print "%s, %-5s, height," % (image.name, color),
            stats([ymax-ymin for xmin,ymin,xmax,ymax in bounds])
        print

dataset, classes = pstf.algum.load_dataset(args.dataset, transforms='none')

if args.stats:
    dump_all_the_stats(dataset)
    sys.exit()

features = map(int, args.features.split(','))
if 0 in features:
    features = range(1,len(classes))

d = []
print >>sys.stderr, "analyzing",
for image in dataset:
    for feature in features:    
        label = (image.label == feature)
        items = pymorph.label(label)

        areas = pymorph.blob(items, 'area', 'data')
        d.extend(2*math.sqrt(a/math.pi) for a in areas)
    print >>sys.stderr, '.',
print >>sys.stderr
print "Mean feature diameter", np.mean(d)
    
