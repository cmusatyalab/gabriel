import setuptools


DESCRIPTION = "Protocol for the Gabriel framework"


setuptools.setup(
    name="gabriel-protocol",
    version="2.0.1",
    author="Roger Iyengar",
    author_email="ri@rogeriyengar.com",
    description=DESCRIPTION,
    url="http://gabriel.cs.cmu.edu",
    packages=setuptools.find_packages("src"),
    package_dir={"": "src"},
    license="Apache",
    classifiers=[
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.5',
    install_requires=[
        "protobuf>=3.12",
    ],
)
