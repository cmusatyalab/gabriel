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

from cStringIO import StringIO
import json
import logging
import numpy as np
from PIL import ImageColor
import re
from zipfile import ZipFile
from .image import LabeledImage, make_palette
from .schemas import validate_dataset_properties

log = logging.getLogger('pstf.algum')

class ZipLabeledImage(LabeledImage):
    """ Lazily access labeled image data stored in a zip archive. """
    def __init__(self, zipfile, *args, **kwargs):
        super(ZipLabeledImage, self).__init__(*args, **kwargs)
        self.archive = zipfile

    def load(self, image):
        image = StringIO(self.archive.read(image))
        return super(ZipLabeledImage, self).load(image)

class TransformedImage(LabeledImage):
    def __init__(self, labeledimage, transform):
        super(TransformedImage, self).__init__(labeledimage)
        self.transform = transform
        self.name = labeledimage.name

    def get_image(self):
        return self.transform(self._image.image)

    def get_label(self):
        return self.transform(self._image.label)

def index2color(index):
    assert index >= 0 and index <= 24
    colors = "000f000f000fff00fff0ff808f00f808f80ff08" + \
             "f40fc0cf04f00f40fc0cf04f40fc0ff0cf04"
    return '#'+colors[index*3:(index+1)*3]

class Dataset(object):
    def __init__(self, dataset, remap=[]):
        # load dataset metadata
        self.zipfile = ZipFile(dataset)
        try:
            self.properties = json.load(self.zipfile.open("properties.json"))
        except KeyError:
            self.properties = self.fallback_loader()

        # validate properties
        try:
            validate_dataset_properties(self.properties)
        except ValueError, error:
            log.critical(error)
            assert False, "validation failed"

        # initialize classes and colormap if they don't exist
        self.classes = self.properties.setdefault('classes', ['unlabeled'])
        self.colormap = self.properties.setdefault('colormap',
                                              {'unlabeled': ['#000']})

        # make sure all classes are accounted for
        extra_classes = []
        for props in self.properties['images'].values():
            for label in props.get('labels', []):
                if not isinstance(label, tuple):
                    continue
                label, colormap = label
                extra_classes.extend(colormap.keys())

        # add as yet unlisted classes and assign them a color value
        # (we blindly assume we allocate colors the same way everywhere)
        extra_classes.sort()
        for cls in extra_classes:
            if cls in self.classes:
                continue
            self.classes.append(cls)

        self.remap = {}
        for lbl, cls in remap:
            if lbl in self.classes:
                if lbl != 'unlabeled':
                    self.classes.remove(lbl)
                if cls not in self.classes:
                    self.classes.append(cls)
                self.remap[lbl] = cls

    def __getattr__(self, name):
        return self.properties[name]

    def fallback_loader(self):
        # build a properties dict for trainingset.zip files without properties
        properties = {}
        manifest = self.zipfile.read('hyperfind-manifest.txt')
        properties['name'] = manifest.split(':', 1)[1].strip()

        classes, colormap = [], {}
        for line in self.zipfile.open('labels.txt'):
            if line.startswith(';'):
                continue

            color, label = line.split(None, 1)
            label = label.strip()
            if label not in classes:
                classes.append(label)
                colormap[label] = [color]
            else:
                colormap[label].append(color)
        properties['classes'] = classes
        properties['colormap'] = colormap

        images = {}
        namelist = self.zipfile.namelist()
        for name in namelist:
            m = re.match('^(.*)-image.*$', name)
            if not m:
                continue

            label_prefix = m.group(1) + "-label."
            for label in namelist:
                if label.startswith(label_prefix):
                    images[name] = { 'labels': label }
                    break
        properties['images'] = images
        return properties

    @property
    def labels_txt(self):
        labels_txt = []
        for label in self.classes:
            for color in self.colormap.get(label, []):
                labels_txt.append('%s %s' % (color, label))
        return '\n'.join(labels_txt)

    @property
    def labels(self):
        labels = []
        for lbl, colors in self.colormap.items():
            cls = self.remap.get(lbl, lbl)
            for color in colors:
                labels.append((cls, ImageColor.getrgb(color)))
        return labels


def load_dataset(trainingset, transforms='all', remap=[], unlabeled=0, versus=0,
                 **kwargs):
    d = Dataset(trainingset, remap)

    if transforms == 'all':
        transforms = [
        lambda x: x[:,::-1],                # horizontal flip
        lambda x: np.rollaxis(x,1)[:,::-1], # 90 degree rotation
        lambda x: np.rollaxis(x[:,::-1],1), # -90 degree rotation
        lambda x: np.rollaxis(x[:,::-1],1)[:,::-1], # 90 rotation of flipped img
        lambda x: np.rollaxis(x,1),         # -90 degree rotation of flipped img
        lambda x: x[::-1,::-1],             # 180 degree rotation
        lambda x: x[::-1],                  # 180 degree rotation of flipped img
        ]
    elif transforms == 'hflip':
        transforms = [
        lambda x: x[:,::-1],                # horizontal flip
        ]
    elif transforms == 'none':
        transforms = []

    image_props = d.images
    palette = make_palette(d.labels, d.classes, unlabeled, versus)

    images = []
    for image, props in image_props.items():
        for label in props.get('labels', []):
            img = ZipLabeledImage(zipfile=d.zipfile, image=image, label=label,
                                  properties=props, palette=palette, **kwargs)
            for transform in transforms:
                images.append(TransformedImage(img, transform))
            images.append(img)
    return images, d.classes


def make_diamond_predicate_xml(name, classes, have_SVM=True):
    from opendiamond.bundle import element, validate_manifest, format_manifest

    display_name = 'STF: %s' % name

    classes = [ (name.capitalize(), i)
                for i, name in enumerate(classes[1:], 1) ]
    classes.sort()

    def member(filename):
        return element('member', filename=filename, data=filename)

    if have_SVM:
        SVM_DATA = [ member(filename="svmmodel%d" % i)
                     for i in xrange(1,len(classes)) ]
        SVM_DATA.append(member(filename='bosts.npy'))
    else:
        SVM_DATA = []

    root = element('predicate',
        element('options',
            element('optionGroup',
                element('choiceOption',
                    *[ element('choice', displayName=name, value=value)
                       for name, value in classes ],
                    displayName="Class", name="label_class"
                ),
                element('booleanOption', displayName='Scale input to 320x320',
                        name='resize', default="false"),
                element('booleanOption',
                        displayName='Ignore lower-confidence regions',
                        name='argmax', default="true"),
                displayName='STF parameters',
            ),
            element('optionGroup',
                element('numberOption', displayName='Confidence threshold',
                        name='threshold', min=0, max=255, step=1, default=1,
                        initiallyEnabled="true"),
                element('numberOption', displayName='Hole-filling feature size',
                        name='close_size', min=1, max=30, step=1, default=5,
                        initiallyEnabled="false"),
                element('numberOption', displayName='Noise removal feature size',
                        name='open_size', min=1, max=30, step=1, default=5,
                        initiallyEnabled="true"),
                element('booleanOption', name='size_ratio', default="false",
                        displayName='Show statistics as percentage of image size'),
                element('booleanOption', name='distribution', default="false",
                        displayName='Compute variability of statistics across image'),
                element('booleanOption', displayName='Draw bounding boxes',
                        name='locate_blobs', default="false"),
                displayName='Post processing',
            ),
        ),
        element('filters',
            element('filter',
                element('minScore', value=1),
                element('dependencies',
                    element('dependency', fixedName="RGB"),
                ),
                element('arguments',
                    element('argument', value="STFFilter"),
                    element('argument', option="label_class"),
                    element('argument', option="resize"),
                    element('argument', option="argmax"),
                    element('argument', option="threshold"),
                ),
                element('blob',
                    member(filename='forest0.npz'),
                    member(filename='forest1.npz'),
                    member(filename='hist0.npy'),
                    member(filename='hist1.npy'),
                    *SVM_DATA
                ),
                code="python-stf.zip",
                label="STF",
            ),
            element('filter',
                element('minScore', value=1),
                element('dependencies',
                    element('dependency', label="STF"),
                ),
                element('arguments',
                    element('argument', value="PostProcessFilter"),
                    element('argument', label="STF"),
                    element('argument', option="threshold"),
                    element('argument', option="close_size"),
                    element('argument', option="open_size"),
                    element('argument', option="size_ratio"),
                    element('argument', option="distribution"),
                    element('argument', option="locate_blobs"),
                ),
                code="python-stf.zip",
                label="PostProcess",
            ),
        ),
        displayName=display_name
    )
    #validate_manifest(root)
    return format_manifest(root)

