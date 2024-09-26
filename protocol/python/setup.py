import setuptools


DESCRIPTION = "Protocol for the Gabriel framework"

setuptools.setup(
    name="gabriel-protocol",
    version="3.0",
    author="Roger Iyengar",
    author_email="ri@rogeriyengar.com",
    maintainer="CMU Satyalab",
    maintainer_email="gabriel@cmusatyalab.org",
    description=DESCRIPTION,
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
        "protobuf>=3.12",
    ],
)
