import setuptools


with open("README.md", "r") as fh:
    long_description = fh.read()


setuptools.setup(
    name="gabriel-server",
    version="2.0.2",
    author="Roger Iyengar",
    author_email="ri@rogeriyengar.com",
    description="Server for Wearable Cognitive Assistance Applications",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="http://gabriel.cs.cmu.edu",
    packages=setuptools.find_packages("src"),
    package_dir={"": "src"},
    license="Apache",
    classifiers=[
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.6",
    install_requires=[
        "gabriel-protocol==2.0.1",
        "websockets==9.1",
        "pyzmq==18.1",
    ],
)
