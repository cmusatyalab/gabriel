#!/usr/bin/env python
#
# Gabriel: Cognitive Assistance System
#
#   author: Kiryong Ha <krha@cmu.edu>
#           Zhuo Chen <zhuoc@cs.cmue.edu>
#           Wenlu Hu <wenlu@cs.cmue.edu>
#
#   copyright (c) 2013-2014 Carnegie Mellon University
#   licensed under the apache license, version 2.0 (the "license");
#   you may not use this file except in compliance with the license.
#   you may obtain a copy of the license at
#
#       http://www.apache.org/licenses/license-2.0
#
#   unless required by applicable law or agreed to in writing, software
#   distributed under the license is distributed on an "as is" basis,
#   without warranties or conditions of any kind, either express or implied.
#   see the license for the specific language governing permissions and
#   limitations under the license.
#

import os
from gabriel.common.config import Const

from distutils.core import setup

# get all executable file
def get_all_files(package_dir, target_path, exclude_names=list()):
    data_files = list()
    cur_dir = os.path.abspath(os.curdir)
    os.chdir(package_dir)
    for (dirpath, dirnames, filenames) in os.walk(target_path):
        for filename in filenames:
            if filename.startswith('.') == True:
                continue
            if filename in exclude_names:
                continue
            data_files.append(os.path.join(dirpath, filename))
    os.chdir(cur_dir)
    return data_files

script_files = get_all_files(".", "bin")
executable_files = get_all_files('.', 'gabriel/lib')

setup(
        name='elijah-gabriel',
        version="0.1.0",
        description='Gabriel: Cognitive Assistance System',
        #long_description=open('README.md', 'r').read(),
        url='https://github.com/cmusatyalab/gabriel',
        author='Kiryong Ha',
        author_email='krha@cmu.edu',
        keywords="cloud cloudlet CMU Gabriel Congitive",
        license='Apache License Version 2.0',
        scripts=script_files+executable_files,
        packages=[
            'gabriel',
            'gabriel.control',
            'gabriel.ucomm',
            'gabriel.common',
            'gabriel.common.network',
            'gabriel.proxy',
            ],
        package_data={'gabriel': ['control/*.html']},
        data_files=[],
        requires=[],
        classifiers=[
            'Programming Language :: Python :: 2.7'
        ]
)


