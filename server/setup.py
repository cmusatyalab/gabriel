"""Setup script for the Gabriel server package."""

import setuptools

with open("README.md") as fh:
    long_description = fh.read()


setuptools.setup(
    name="gabriel-server",
    version="4.0.5",
    author="Aditya Chanana",
    author_email="achanana@cs.cmu.edu",
    maintainer="CMU Satyalab",
    maintainer_email="gabriel@cmusatyalab.org",
    description="Server for Wearable Cognitive Assistance Applications",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="http://gabriel.cs.cmu.edu",
    packages=setuptools.find_packages("src"),
    package_dir={"": "src"},
    license="Apache",
    classifiers=[
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    install_requires=[
        "gabriel-protocol==4.0",
        "websockets>=13.0",
        "pyzmq>=18.1",
    ],
)
