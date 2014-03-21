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

HOME_DIR = "cloudlet/gabriel/"
CONTROL_DIR = "./src/control"
UCOMM_DIR = "./src/ucomm"
APP_COMM_DIR = "./src/app/common"
COMMON_FILES = ["./src/control/__init__.py",
                "./src/control/protocol.py",
                "./src/control/config.py",
                "./src/control/upnp_client.py",
                "./src/control/log.py",
                "./src/control/lib/gabriel_upnp_client.jar",
                ]


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

    sys.stdout.write("[SUCCESS] Finish installing Gabriel\n")


def _tar_dir(tarballname, dirname):
    if run("tar -cvf %s %s" % (tarballname, dirname)).failed:
        msg = "Failed to compress directories:\n"
        msg += "%s\n" % (CONTROL_DIR)
        abort(msg)


def _tar_files(tarballname, filelist, exclude_list=list()):
    filelist_str = ' '.join(filelist)
    exclude_str = ''
    for item in exclude_list:
        exclude_str += '--exclude=%s ' % item
    if run("tar cvfz %s %s %s" % (tarballname, filelist_str, exclude_str)).failed:
        msg = "Failed to compress directories:\n"
        msg += "%s\n" % (filelist_str)
        abort(msg)


@task 
def packaging():
    global HOME_DIR
    global CONTROL_DIR
    global UCOMM_DIR
    global APP_COMM_DIR

    packaging_control()
    packaging_ucomm()
    packaging_appcomm()


@task
def packaging_control():
    global HOME_DIR
    global CONTROL_DIR

    tarfile_name = "gabriel-control.tar.gz"
    with cd(HOME_DIR):
        _tar_dir(tarfile_name, CONTROL_DIR) 

@task
def packaging_ucomm():
    global HOME_DIR
    global UCOMM_DIR
    global COMMON_FILES

    tarballname = "gabriel-ucomm.tar.gz"
    with cd(HOME_DIR):
        filelist = COMMON_FILES + [UCOMM_DIR]
        _tar_files(tarballname, filelist)


@task
def packaging_appcomm():
    global HOME_DIR
    global APP_COMM_DIR
    global COMMON_FILES

    tarballname = "gabriel-appcomm.tar.gz"
    with cd(HOME_DIR):
        filelist = COMMON_FILES + [APP_COMM_DIR]
        _tar_files(tarballname, filelist)


@task
def packaging_apps():
    global HOME_DIR
    global APP_COMM_DIR
    global COMMON_FILES

    with cd(HOME_DIR):
        '''
        # dummy
        filelist = COMMON_FILES + [APP_COMM_DIR] + ["./src/app/http_display/"]
        _tar_files("gabriel-dummy.tar.gz", filelist)
        '''

        # face recognition
        filelist = COMMON_FILES + ["./src/app/face_recognition/"]
        _tar_files("gabriel-face.tar.gz", filelist)

        # moped
        filelist = COMMON_FILES + [APP_COMM_DIR] + ["./src/app/object_moped/"]
        _tar_files("gabriel-moped.tar.gz", filelist)

        # OCR (open source)
        filelist = COMMON_FILES + [APP_COMM_DIR] + ["./src/app/ocr/"]
        exclude_list = ["./src/app/ocr/results", "./src/app/ocr/glass-client"]
        _tar_files("gabriel-ocr.tar.gz", filelist, exclude_list=exclude_list)
