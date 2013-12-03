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
from cStringIO import StringIO
from fabric.api import env, execute, run, put, get, hosts
from fabric.main import load_settings
from itertools import izip
import logging
import numpy as np 
import os
from zipfile import ZipFile, ZIP_DEFLATED

import pstf
from pstf.scripts.train_tree0 import forest0_args, train_tree0
from pstf.scripts.train_tree1 import forest1_args, DistImage
from pstf_training_aws import ec2_task, ec2_instances, terminate_all, shutdown

log = logging.getLogger('pstf.train_classifier')

EC2_AMI = 'ami-5f3be236'
EC2_TYPE = 'm1.medium'


def make_forest(trees):
    # merge individual trees to create a Forest
    trees = [ pstf.forest.Forest.load(t) for t in trees ]
    return pstf.forest.Forest(trees=trees)

# save STF forest to zip archive
def save_forest_to_classifier(classifier, name, forest):
    classifier = ZipFile(classifier, 'a', compression=ZIP_DEFLATED)
    f = StringIO()
    forest.save(f)
    classifier.writestr(name, f.getvalue())
    classifier.close()

# save numpy array to zip archive
def save_ndarray(archive, name, data):
    archive = ZipFile(archive, 'a', compression=ZIP_DEFLATED)
    f = StringIO()
    np.save(f, data)
    archive.writestr(name, f.getvalue())
    archive.close()


@ec2_task
@hosts(ec2_instances(EC2_AMI, EC2_TYPE, 5))
def ec2_train(tree, args):
    put(args.classifier, 'stf_classifier.pred')
    put(args.trainingset, 'trainingset.zip')

    run('unzip -o stf_classifier.pred config.txt python-stf.zip')
    run('python python-stf.zip train_%s @config.txt --seed=%d'
        ' trainingset.zip stf_classifier.pred tree.npz' % (tree, env.index))

    get('tree.npz', '%s_%d.npz' % (tree, env.index))

    #result = StringIO()
    #get('tree.npz', result)
    #return result.getvalue()


def fetch(training):
    import requests
    import uuid
    try:
        r = requests.get(training)
        r.raise_for_status()
        try:
            os.mkdir('trainingdata')
        except OSError:
            pass
        training = 'trainingdata/%s.zip' % uuid.uuid1()
        with open(training, 'wb') as f:
            for chunk in r.iter_content():
                f.write(chunk)
    except ValueError:
        pass
    return training

def store(dest, source):
    import requests
    try:
        with open(source, 'rb') as f:
            requests.put(dest, data=f.read())
    except ValueError:
        os.rename(source, dest)


def split_leafimage(leafimage, nsplits):
    images = ( image
               for split in np.array_split(leafimage.image, nsplits, axis=1)
               for image in np.array_split(split, nsplits, axis=2) )
    labels = ( label
               for split in np.array_split(leafimage.label, nsplits, axis=0)
               for label in np.array_split(split, nsplits, axis=1) )

    HISTLEN, STRIDE = leafimage.HISTLEN, leafimage.STRIDE
    for image, label in izip(images, labels):
        yield pstf.forest.LeafImage(image, label, HISTLEN, STRIDE)

def fill_forest0(forest0, args):
    log.info("Loading and converting training images for filling")
    trainingset, classes = pstf.algum.load_dataset(args.trainingset,
            transforms=args.transforms, unlabeled=args.unlabeled,
            versus=args.versus, remap=args.remap, resize=args.resize)
    NCLASSES = len(classes)

    stage = "Computing forest0 leaf images"
    progress = pstf.utils.Progress(stage, len(trainingset))
    STRIDE = args.forest0_filling_sample_frequency
    leafset = []
    for labeledimage in trainingset:
        leafimage = forest0.compute_leafimage(labeledimage, STRIDE)
        leafset.append(leafimage)
        labeledimage.drop_caches()
        progress.update(1)
    progress.done()

    stage = "Computing forest0 histograms"
    progress = pstf.utils.Progress(stage, len(leafset))
    hist0 = forest0.build_histogram(leafset, NCLASSES, progress=progress)
    save_ndarray(args.classifier, "hist0.npy", hist0)
    progress.done()

    hist0 = np.insert(pstf.forest.normalize(hist0), 0, 0, axis=0)
    distset = [ DistImage(leafimage, forest0, hist0) for leafimage in leafset ]

    if args.svm_split > 1:
        leafset = [ image for leafimage in leafset
                    for image in split_leafimage(leafimage, args.svm_split) ]

    stage = "Computing image classes"
    progress = pstf.utils.Progress(stage, len(leafset))
    image_classes = forest0.build_classes(leafset, NCLASSES, progress=progress)
    save_ndarray(args.classifier, "classes.npy", image_classes)
    progress.done()

    # test if each class is either all true or false for all images in leafset
    build_SVM = not (np.all(image_classes, axis=1) |
                     ~np.any(image_classes, axis=1)).all()
    if not build_SVM:
        log.warning("Not training SVM classifier, all images contain all labels")
    else:
        stage = "Computing BoSTs"
        progress = pstf.utils.Progress(stage, len(leafset))
        bosts = forest0.build_bosts(leafset, progress=progress)
        save_ndarray(args.classifier, "bosts.npy", bosts)
        progress.done()

        bosts = pstf.forest.normalize(bosts).T

        stage = "Creating SVM training vectors"
        progress = pstf.utils.Progress(stage)
        vectors = pstf.svm_.make_vectors(bosts, bosts, progress)
        progress.done()

        if args.debug:
            np.save('svm_vectors.npy', vectors)

        stage = "Training SVM classifier"
        progress = pstf.utils.Progress(stage, NCLASSES-1)
        for idx in range(1, NCLASSES):
            class_labels = image_classes[idx] * 2.0 - 1.0
            model_file = "svmmodel%d" % idx

            pstf.svm_.svm_train(class_labels, vectors, model_file, progress)

            classifier = ZipFile(args.classifier, 'a', compression=ZIP_DEFLATED)
            classifier.write(model_file)
            os.unlink(model_file)
            classifier.close()
        progress.done()

    return distset, NCLASSES, build_SVM

def fill_forest1(distset, NCLASSES, forest1, args):
    stage = "Computing forest1 histograms"
    progress = pstf.utils.Progress(stage, len(distset))
    hist1 = forest1.build_histogram(distset, NCLASSES,
                                    args.forest1_filling_sample_frequency,
                                    IS_INTEGRAL=True, progress=progress)
    save_ndarray(args.classifier, "hist1.npy", hist1)
    progress.done()


if __name__ == '__main__':
    parser = pstf.utils.ArgumentParser(description="Train an STF classifier",
                 parents=[pstf.utils.stf_common_args,forest0_args,forest1_args],
                 fromfile_prefix_chars='@',
                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('output', nargs='?', default="stf_classifier.pred",
                        help="output classifier predicate file")

    svm_args = parser.add_argument_group("SVM specific settings")
    svm_args.add_argument('--svm-split', metavar="N", type=int, default=1,
                          help="divide input into N by N pieces for SVM training")

    debug = parser.add_argument_group('debugging', """You should never need to
        use these. If you think you do need to use some of these you probably
        don't know what you are doing.""")
    debug.add_argument('--skip-forest0', action="store_true",
        help="""skip training forest0, we will still pretend the trees we find
        were built in the cloud""")
    debug.add_argument('--skip-forest1', action="store_true",
        help="""skip training forest1, we will still pretend the trees we find
        were built in the cloud""")

    env.update(load_settings(os.path.expanduser('~/.stfrc')))
    args = parser.parse_args()
    pstf.utils.log_args(args)

    args.trainingset = fetch(args.trainingset)
    args.classifier = os.path.splitext(args.trainingset)[0] + '.pred'

    # run a single datapoint, one node training to test common code paths
    stage = "Running smoke tests"
    progress = pstf.utils.Progress(stage)
    smoke_args = argparse.Namespace(**vars(args))
    smoke_args.forest0_depth=2
    smoke_args.forest0_training_sample_frequency=8
    smoke_args.forest0_image_probability=0.001
    smoke_args.forest0_datapoint_probability=0.001
    logging.disable(logging.INFO)
    train_tree0(0, smoke_args)
    logging.disable(logging.NOTSET)
    progress.done()

    trainingset = pstf.algum.Dataset(args.trainingset, args.remap)

    # create diamond filter executable with python-stf source
    diamond_filter_file = StringIO()
    diamond_filter_file.write("#!/usr/bin/env python\n")
    diamond_filter = ZipFile(diamond_filter_file, 'a', compression=ZIP_DEFLATED)
    diamond_filter.write('loader.py', '__main__.py')
    diamond_filter.write('python-stf.tar.gz')
    diamond_filter.close()

    # create initial predicate containing configuration and diamond filter
    classifier = ZipFile(args.classifier, 'w', compression=ZIP_DEFLATED)
    classifier.writestr('config.txt', pstf.utils.serialize_args(args))
    classifier.writestr('python-stf.zip', diamond_filter_file.getvalue())
    classifier.write('loader.py', '__main__.py')

    # these should get folded into the python-stf source as well
    classifier.write('stf_train_classifier.py')
    classifier.write('pstf_training_aws.py')
    classifier.close()

    try:
        if not args.skip_forest0:
            stage = "Training forest0 trees"
            progress = pstf.utils.Progress(stage, args.forest0_trees)
            results = execute(ec2_train, 'tree0', args,
                hosts=ec2_instances(EC2_AMI, EC2_TYPE, args.forest0_trees)
            )
            progress.done()
        #terminate_all()

        results = [ "tree0_%d.npz" % i for i in xrange(args.forest0_trees) ]
        forest0 = make_forest(results)
        save_forest_to_classifier(args.classifier, 'forest0.npz', forest0)

        distset, NCLASSES, have_SVM = fill_forest0(forest0, args)

        if not args.skip_forest1:
            stage = "Training forest1 trees"
            progress = pstf.utils.Progress(stage, args.forest1_trees)
            results = execute(ec2_train, 'tree1', args,
                hosts=ec2_instances(EC2_AMI, EC2_TYPE, args.forest1_trees)
            )
            progress.done()
        terminate_all()

        results = [ "tree1_%d.npz" % i for i in xrange(args.forest1_trees) ]
        forest1 = make_forest(results)
        save_forest_to_classifier(args.classifier, 'forest1.npz', forest1)

        fill_forest1(distset, NCLASSES, forest1, args)
    finally:
        shutdown()

    stage = "Finalizing Diamond STF classifier"
    progress = pstf.utils.Progress(stage)
    manifest = pstf.algum.make_diamond_predicate_xml(trainingset.name,
                                                     trainingset.classes,
                                                     have_SVM)

    classifier = ZipFile(args.classifier, 'a', compression=ZIP_DEFLATED)
    classifier.writestr('opendiamond-bundle.xml', manifest)
    classifier.close()
    progress.done()

    store(args.output, args.classifier)

