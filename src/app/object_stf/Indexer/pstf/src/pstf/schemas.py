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
import json

#
# Schema for the properties.json file that is used to describe an
# Algum dataset.
#
colormap = {
    'description': "Class name to color value mapping",
    'type': 'object',
    'additionalProperties': {
        'type': 'array',
        'minItems': 1,
        'items': {
            'description': "Color value",
            'type': 'string',
            'format': 'color'
        }
    }
}
label = { 'type': 'string' }
label_tuple = {
    'description': "Image label and color map tuple",
    'type': 'array',
    'items': [ label, colormap ],
    'additionalItems': False
}
svd_d = {
    'description': "Singular values",
    'type': 'array',
    'minItems': 3, 'maxItems': 3,
    'items': { 'type': 'number' }
}
svd_Vt = {
    'description': "Conjugate transpose of right-singular vectors",
    'type': 'array',
    'minItems': 3, 'maxItems': 3,
    'items': {
        'type': 'array',
        'minItems': 3, 'maxItems': 3,
        'items': { 'type': 'number' }
    }
}
image_properties = {
    'description': "Image metadata",
    'type': 'object',
    'properties': {
        'labels': {
            'type': 'array',
            'items': { 'type': [ label, label_tuple ] },
        },
        'svd': {
            'description': "Singular value decomposition components",
            'type': 'object',
            'properties': { 'd': svd_d, 'Vt': svd_Vt, },
            'additionalProperties': False,
            'required': False
        }
    }
}
dataset_properties = {
    'description': "A descriptor for a collection of labeled image data",
    'type': 'object',
    'properties': {
        'name': {
            'title': "Collection name",
            'description': "Describes where the data originated",
            'type': 'string',
            'required': False
         },
        'images': {
            'description': "A set of images, keys are image names",
            'type': 'object',
            'additionalProperties': image_properties
        },
        'classes': {
            'description': "List of classes in the dataset",
            'type': 'array',
            'items': label,
            'uniqueItems': True,
            # we may construct this list using sort/uniq,
            # 'unlabeled' should always be the first entry.
            'required': False
        },
        'colormap': {
            # used as a default when individual labels do not specify a color
            # map as well as whenever we try to create an output image
            'description': "Default class label to color mapping",
            'type': colormap,
            # we can construct this based on the list of known classes and some
            # default set of color values
            'required': False
        }
    },
    'additionalProperties': False
}

try:
    import validictory
    def validate_dataset_properties(properties):
        validictory.validate(properties, dataset_properties)

except ImportError:
    def validate_dataset_properties(properties):
        pass

if __name__ == '__main__':
    import argparse
    import zipfile

    parser = argparse.ArgumentParser()
    parser.add_argument('--dump', action="store_true",
                        help="dump json-schema")
    parser.add_argument('dataset', nargs='?',
                        help="specify dataset to validate")
    args = parser.parse_args()

    if not args.dump and not args.dataset:
        parser.error("specify at least one of --dump or a dataset to validate")

    if args.dump:
        print json.dumps(dataset_properties, sort_keys=True, indent=4)

    if args.dataset:
        f = zipfile.ZipFile(args.dataset).open('properties.json')
        try:
            validate_dataset_properties(json.load(f))
        except ValueError, error:
            print "validation failed", error
        else:
            print "validated"

