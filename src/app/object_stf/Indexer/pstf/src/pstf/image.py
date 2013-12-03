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

from PIL import Image
import logging
import numpy as np
from .utils import LazyProperty

log = logging.getLogger('pstf.image')

# Color space conversions from www.easyrgb.com

# Observer = 2degrees, Illuminant = D65
sRGB_to_XYZ = np.transpose([[0.4124, 0.3576, 0.1805],
                            [0.2126, 0.7152, 0.0722],
                            [0.0193, 0.1192, 0.9505]])

# XYZ Reference values of a perfect reflecting diffuser
# CIE 1931, Observer = 2degrees, Illuminant = D65
XYZref = [95.047, 100.000, 108.883]

XYZ_to_Lab = np.transpose([[  0,  116,    0],
                           [500, -500,    0],
                           [  0,  200, -200]])

def convert_to_cielab(img):
    ### RGB to XYZ conversion
    img = img / 255.0
    # commented this code, assume images are already device-independent sRGB
    #mask = img > 0.04045
    #img = np.piecewise(img, [mask, ~mask],
    #                   [lambda x: ((x+0.055)/1.055)**2.4,
    #                    lambda x: x/12.92])
    img *= 100.0
    img = np.dot(img, sRGB_to_XYZ) # img = XYZ

    ### XYZ to CIE-L*a*b* conversion
    img /= XYZref
    mask = img > 0.008856
    img = np.piecewise(img, [mask, ~mask],
                       [lambda x: x**(1.0/3),
                        lambda x: (x*7.787) + (16.0/116)])

    img = np.dot(img, XYZ_to_Lab) # img = Lab
    img[..., 0] -= 16.0

    # normalize to [0,255]
    img += [16.0, 500.0, 200.0]
    img *= [(255.0 / 116), (255.0 / 1000), (255.0 / 400)]
    return img


def convert_to_primaries(rgb, Vt=None):
    img = (rgb / 255.0)
    img -= 0.5
    img = img.reshape(-1, 3)

    if Vt is not None:
        Vt = np.asarray(Vt)
    else:
        _, _, Vt = np.linalg.svd(img, full_matrices=False)
    V = -np.sign(np.diag(Vt)) * Vt.T

    img = np.dot(img, V)
    img.shape = rgb.shape
    img += 1.0
    img *= (255.0 / 2.0)
    return img

try:
    from scipy.cluster.vq import vq
    def vector_quantization(image, label_colors):
        features = label_colors.shape[1]
        data = np.asarray(image.convert('RGB')).reshape((-1, features))
        return vq(data, label_colors)[0].reshape(image.size)

except ImportError:
    def vector_quantization(image, label_colors):
        features = label_colors.shape[1]
        data = np.asarray(image.convert('RGB')).reshape((-1, features))
        diff = data[np.newaxis, :, :] - label_colors[:, np.newaxis, :]
        dist = np.sum(diff * diff, axis=-1)
        return dist.argmin(axis=0).reshape(image.size)

class LabeledImage(object):
    def __init__(self, image, label=None, properties=None,
                 palette=None, resize=False):
        """ Object that holds both image and corresponding label data.

        Looks like a named tuple, but lazily loads the data for
        obj.image and obj.label. Palette is used to map the colors in
        the label image to the correct/closest class index value.
        """
        self.name = image
        self._image = image
        self._label = label
        self.properties = properties or {}
        palette = palette or (None, range(256)) # # of labels in uint8
        self.palette = palette[0]
        self.remap = np.asarray(palette[1])
        self.resize = resize

    def load(self, image):
        image = Image.open(image)
        if self.resize:
            image.thumbnail((320, 320), Image.ANTIALIAS)
        return image

    def get_image(self):
        """ Load image and convert to CIELab color space.

        Returns a numpy array that stores for each pixel the L* a* b*
        and R G B color channels.
        """
        image = self.load(self._image)
        Vt = self.properties.get('svd', {}).get('Vt', None)

        if Vt is None:
            log.warn("Computing primary components for %s", self.name)

        rgb = np.asarray(image.convert('RGB'), dtype='float')
        Lab = convert_to_cielab(rgb)
        pcv = convert_to_primaries(rgb, Vt)

        return np.dstack((Lab, rgb, pcv))

    def get_label(self):
        """ Load and normalize a label image.

        Maps the label colors to the values of the classes specified in
        labels.txt. Returns a numpy array where each pixel is labeled
        based on the index of the corresponding class index.
        """
        if self.palette:
            # make sure transparent/unlabeled data maps to black
            data = self.load(self._label).convert('RGBA')
            label = Image.new('RGB', data.size)
            label.paste(data, None, data)

            label.load()
            im = label.im.convert("P", Image.NONE, self.palette.im)
            label = label._makeself(im)

            # quantize
            #quant = vector_quantization(label, label_colors)
            #labels = np.zeros(label.size+(len(label_colors),), dtype='bool')
            #labels[..., quant] = True
        else:
            # we don't have a palette to map to, assume the image
            # already was using a correct palette.
            label = self.load(self._label).convert("P")

        return self.remap[np.asarray(label)]

    @LazyProperty
    def image(self):
        return self.get_image()

    @LazyProperty
    def label(self):
        return self.get_label()

    def drop_caches(self):
        self.__dict__.pop("image", None)
        self.__dict__.pop("label", None)


def expand_border(img, BORDER, IS_INTEGRAL=False):
    t = img[0:1] * (not IS_INTEGRAL)
    b = img[-2:-1]
    img = np.vstack((t.repeat(BORDER, axis=0), img, b.repeat(BORDER, axis=0)))

    l = img[:, 0:1] * (not IS_INTEGRAL)
    r = img[:, -2:-1]
    img = np.hstack((l.repeat(BORDER, axis=1), img, r.repeat(BORDER, axis=1)))
    return img


def make_palette(labels, classes, unlabeled=0, versus=0):
    # build map to generate a palette, we need to pad with 0's, but not too
    # many to avoid segfault when quantizing
    # 768 == 256*3 --> 256 possible colors, 3 bytes each (RGB) default in PIL
    colormap = [ value for _, color in labels for value in color ]+[0]*768
    colormap = colormap[:768]

    palette = Image.new("P", (1, 1))
    palette.putpalette(colormap)

    classmap = [ classes.index(label) for label, _ in labels ]

    # apply any old style remappings
    if unlabeled:
        classmap = [ value if value != 0 else unlabeled
                     for value in classmap ]
    if versus:
        opposite = (versus - 1) or (len(classes) - 1)
        classmap = [ value if value == 0 or value == versus else opposite
                     for value in classmap ]

    return palette, classmap


# generate class -> color colormap to give unique colors for 30 classes in
# a classifier output image.
output_colormap = [ int(c, 16)*17 for c in "000f000f000f" +
                    "ff00fff0ff808f00f808f80ff08f40fc0cf" +
                    "04f00f40fc0cf04f40fc0ff0cf04" ] + [0]*768
output_colormap = output_colormap[:768]

# should this move to pstf.segmentation?

#def averaging_segmenter(imgs, padding):
#    imgs = expand_border(imgs, padding/2)
#    overlap = np.mod(imgs.shape[:2], padding)
#    l, t = overlap/2
#    r, b = imgs.shape[:2] - (overlap - overlap/2)
#    imgs = imgs[l:r, t:b]
#
#    w, h = imgs.shape[:2] / padding
#    imgs = imgs.reshape(padding, -1, imgs.shape[2]).sum(axis=0)
#    imgs = imgs.reshape(-1, padding, imgs.shape[2]).sum(axis=1)
#    imgs = imgs.reshape(w, h, imgs.shape[2])
#    return imgs.argmax(axis=2)

#import Image
#import pymorph
#
#def morphological_segmenter(imgs, label):
#    mask = imgs.argmax(axis=2) == label
#    img = Image.fromarray(np.uint8(mask) * 255)
#    img = pymorph.close(img)
#    img = pymorph.open(img)
#    return img

