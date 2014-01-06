from __future__ import with_statement

from fabric.api import env
from fabric.api import hide
from fabric.api import run
from fabric.api import local
from fabric.api import sudo
from fabric.api import task
from fabric.api import abort
from fabric.context_managers import cd

import os
import sys


def check_support():
    # check ubuntu
    if run("lsb_release -irc | grep Ubuntu > /dev/null").failed:
        abort("This installation script is designed for Ubuntu distribution")


@task
def localhost():
    env.run = local
    env.warn_only = True
    env.hosts = ['localhost']


@task
def install():
    check_support()

    # install dependent package
    sudo("apt-get update")
    if sudo("apt-get install default-jre python-pip " +
            "pssh python-psutil").failed:
        abort("Failed to install libraries")
    if sudo("sudo pip install flask==0.9 flask-restful==0.2.1 six==1.1.0").failed:
        abort("Failed to install python libraries")

    sys.stdout.write("[SUCCESS] VM synthesis code is installed\n")

