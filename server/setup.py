import setuptools


with open("README.md", "r") as fh:
    long_description = fh.read()


setuptools.setup(
    name="gabriel-server",
    version="3.0",
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
        "programming language :: python :: 3.8",
        "programming language :: python :: 3.9",
        "programming language :: python :: 3.10",
        "programming language :: python :: 3.11",
        "programming language :: python :: 3.12",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "gabriel-protocol==2.0.1",
        "websockets>=9.1",
        "pyzmq>=18.1",
    ],
)
