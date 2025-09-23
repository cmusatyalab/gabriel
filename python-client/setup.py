import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="gabriel-client",
    version="4.0.0",
    author="Aditya Chanana",
    author_email="achanana@cs.cmu.edu",
    maintainer="CMU Satyalab",
    maintainer_email="gabriel@cmusatyalab.org",
    description="Networking components for Gabriel Python clients",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="http://gabriel.cs.cmu.edu",
    packages=setuptools.find_packages("src"),
    package_dir={"": "src"},
    license="Apache",
    classifiers=[
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "gabriel-protocol==4.0",
        "websockets>=9.1",
        "opencv-python>=3, <5",
    ],
    extras_require={
        "zmq": [
            "pyzmq>=18.1",
        ],
    },
)
