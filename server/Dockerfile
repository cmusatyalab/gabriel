FROM nvidia/cuda:8.0-cudnn5-devel
MAINTAINER Satyalab, satya-group@lists.andrew.cmu.edu

ARG DEBIAN_FRONTEND=noninteractive

# Using mirrors is much faster than the standard ubuntu config
RUN echo 'deb mirror://mirrors.ubuntu.com/mirrors.txt xenial main restricted\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-updates main restricted\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial universe\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-updates universe\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial multiverse\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-updates multiverse\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-backports main restricted universe multiverse\n\
deb http://archive.canonical.com/ubuntu/ xenial partner\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-security main restricted\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-security universe\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-security multiverse\n\
deb mirror://mirrors.ubuntu.com/mirrors.txt xenial-proposed multiverse main restricted universe\n\
' > /etc/apt/sources.list

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
    python3.5 \
    python3-pip \
    default-jre \
    pssh \
    python-psutil \
    python-setuptools \
    python-opencv \
    python-matplotlib \
    git

RUN pip2 install -U pip
RUN pip2 install -U setuptools
RUN pip2 install -U numpy

RUN pip3 install -U pip
RUN pip3 install -U setuptools
RUN pip3 install -U numpy

# RUN python3 -m pip install -U pip

# RUN git clone https://github.com/cmusatyalab/gabriel.git
# copy local files instead of cloning, to allow for testing with unpushed code
COPY . /gabriel/server/
WORKDIR /gabriel/server
RUN pip2 install -r requirements.txt
RUN pip3 install -r requirements_python3.txt
RUN python2 setup.py install
RUN python3 setup_python3.py install

RUN apt-get autoremove \
    && apt-get clean

WORKDIR /

EXPOSE 9098 9111 7070 22222

# Define default command.
CMD ["bash", "-c", "gabriel-control -d -n eth0 & sleep 5; gabriel-ucomm -s 127.0.0.1:8021 & sleep 5; cd /gabriel/server/bin/example-proxies/gabriel-proxy-http-display && python proxy.py -s 127.0.0.1:8021"]
