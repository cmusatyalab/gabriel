import argparse
from cStringIO import StringIO
try:
    import Image
except ImportError:
    from PIL import Image
import itertools
import json
import logging
import numpy as np
import os
import pymorph
import zipfile
from ..schemas import validate_dataset_properties

log = logging.getLogger()
IMG_ID = itertools.count()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', default="Trainingdata",
                        help="Descriptive name for training data")
    parser.add_argument('--labels',
                        help="File with color to class name mapping")
    parser.add_argument('--chop', type=int,
                        help="Clip area around labelled blobs")
    parser.add_argument('trainingset', help="output trainingset file")
    parser.add_argument('image label', nargs='+',
                        help="An image with a matching label")
    args = parser.parse_args()
    args.image_and_label = getattr(args, 'image label')

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    properties = {}
    properties['name'] = args.name

    if args.labels:
        classes = []
        colormap = {}
        for line in open(args.labels):
            line = line.strip()
            if not line or line[0] == '#':
                continue
            color, label = line.split(None, 1)
            classes.append(label)
            colors = colormap.setdefault(label, [])
            colors.append(color)
    else:
        classes = [ 'unlabeled', 'red', 'green', 'blue' ]
        colormap = {
            'unlabeled': ['#000'],
            'red':       ['#f00'],
            'green':     ['#0f0'],
            'blue':      ['#00f']
        }

    properties['classes'] = classes
    properties['colormap'] = colormap
    properties['images'] = {}

    log.info('Creating trainingset %s', args.trainingset)
    z = zipfile.ZipFile(args.trainingset, 'w')
    images = args.image_and_label[0::2]
    labels = args.image_and_label[1::2]
    for image, label in itertools.izip(images, labels):
        image_name = os.path.basename(image)
        label_name = os.path.basename(label)

        log.info('Computing SVD for image %s', image_name)

        # precompute primary component values
        img = np.asarray(Image.open(image).convert('RGB')) / 255.
        img -= .5
        img = img.reshape(-1, 3)
        _, d, Vt = np.linalg.svd(img, full_matrices=False)

        if args.chop is None:
            z.write(image, arcname=image_name)
            z.write(label, arcname=label_name)

            properties['images'][image_name] = {
                'labels': [ label_name ],
                'svd': { 'd': d.tolist(), 'Vt': Vt.tolist() }
            }
            continue

        # clip around labelled areas
        bb = args.chop
        pil_image = Image.open(image)
        pil_label = Image.open(label)

        binary = np.asarray(pil_label.convert('RGB')).any(axis=2)
        labels = pymorph.label(binary)
        bounds = pymorph.blob(labels, 'boundingbox', 'data')

        for r, t, l, b in bounds:
            box = (r-bb, t-bb, l+bb, b+bb)
            N = IMG_ID.next()

            image_name = "image-%03d.png" % N
            out = StringIO()
            pil_image.crop(box).save(out, "PNG")
            z.writestr(image_name, out.getvalue())

            label_name = "label-%03d.png" % N
            out = StringIO()
            pil_label.crop(box).save(out, "PNG")
            z.writestr(label_name, out.getvalue())

            properties['images'][image_name] = {
                'labels': [ label_name ],
                'svd': { 'd': d.tolist(), 'Vt': Vt.tolist() }
            }

    validate_dataset_properties(properties)
    z.writestr('properties.json', json.dumps(properties, sort_keys=True,
                                             indent=1))
    z.close()
    log.info('Trainingset created')

if __name__ == '__main__':
    main()

