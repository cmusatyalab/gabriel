try:
    from setuptools import setup, Extension
except ImportError:
    from distutils.core import setup
    from distutils.extension import Extension
try:
    import numpy
    include_dirs = [ numpy.get_include() ]
except ImportError:
    include_dirs = None

stf_innerloop = Extension("pstf.innerloop_cy",
    sources = [ "pstf/innerloop_cy.c" ],
    include_dirs = include_dirs
)

setup(
    name = "python-stf",
    version = "0.1.dev",
    description = "Semantic Texton Forests",
    maintainer = "Jan Harkes",
    maintainer_email = "jaharkes@cs.cmu.edu",
    packages = [ 'pstf', 'pstf.scripts' ],
    ext_modules = [ stf_innerloop ],
    include_package_data = True,
    package_data = { 'pstf': [ '*.pyx' ] },
    zip_safe=True,
    entry_points = {
        'console_scripts': [
            'build_trainingset = pstf.scripts.build_trainingset:main',
            'evaluate_classifier = pstf.scripts.evaluate_classifier:main',
        ],
        'internal_scripts': [
            'train_tree0 = pstf.scripts.train_tree0:main',
            'train_tree1 = pstf.scripts.train_tree1:main',
            '--filter = pstf.scripts.diamond_filter:main',
        ]
    },
    install_requires = [
        'PIL>=1.1.6',
        'PyYAML>=3.08',
        'argparse>=1.1',
        'numpy>=1.6.0',
        'pymorph>=0.96',
        #'Cython>=0.15.1',
        #'libsvm==3.1',
    ],
    extras_require = {
        'TRAIN_CLASSIFIER': [
            'Fabric>=1.4.0',
            'PyYAML>=3.08',
            'boto>=1.2.2',
            'requests>=0.6.6',
            'validictory>=0.8.3',
            #'opendiamond>=7.0.1',
        ],
        'TRAIN_TREE': [
            'PyYAML>=3.08',
        ],
        'EVALUATE_CLASSIFIER': [
            'pymorph>=0.96',
        ],
        'FILTER': [
            'PyYAML>=3.08',
            'pymorph>=0.96',
            #'opendiamond>=7.0.1',
        ],
    },
    dependency_links = [
        #'#egg=libsvm-3.1',
        'http://diamond.cs.cmu.edu/packages/source/opendiamond/opendiamond-7.0.4.tar.gz#egg=opendiamond-7.0.4',
    ],
)

