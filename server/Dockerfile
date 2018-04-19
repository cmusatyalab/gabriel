FROM ubuntu:xenial
MAINTAINER Junjue Wang, junjuew@cs.cmu.edu

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y \
    --no-install-recommends \
    apt-utils

RUN apt-get install -y \
    build-essential \
    pkg-config \
    python2.7 \
    python-dev \
    python-pip \
    default-jre \
    pssh \
    python-psutil \
    python-setuptools \
    python-opencv \
    git

RUN pip install -U pip
RUN pip install -U setuptools
RUN pip install -U numpy

RUN git clone https://github.com/junjuew/gabriel.git
WORKDIR /gabriel/server
RUN pip install -r requirements.txt
RUN python setup.py install

RUN apt-get autoremove \
    && apt-get clean

WORKDIR /gabriel/server/bin

EXPOSE 9098 9111 7070

# Define default command.
CMD ["bash", "-c", "python gabriel-control -n eth0 & sleep 5; python gabriel-ucomm -s 127.0.0.1:8021 & sleep 5; cd example-proxies/gabriel-proxy-http-display && python proxy.py -s 127.0.0.1:8021"]