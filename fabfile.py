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

HOME_DIR = "~/Development/gabriel/"
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
BUILD_DIR = "./dist"

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


def _tar_files(tarballname, filelist, exclude_list=list(), comp_tool="tar"):
    global BUILD_DIR
    
    if os.path.exists(BUILD_DIR) is False:
        os.mkdir(BUILD_DIR)

    filelist_str = ' '.join(filelist)
    exclude_str = ''
    for item in exclude_list:
        exclude_str += '--exclude=%s ' % item
    tarballpath = os.path.join(BUILD_DIR, tarballname)

    command = None
    if comp_tool == "tar":
        command = "tar cvfz %s %s %s" % (tarballpath, filelist_str, exclude_str)
    elif comp_tool == "zip":
        command = "zip -r %s %s %s" % (tarballpath, filelist_str, exclude_str)
    else:
        msg = "Unsupported compression tool name: %s" % comp_tool
        abort(msg)

    if run(command).failed:
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
    packaging_apps()


@task
def packaging_control():
    global HOME_DIR
    global CONTROL_DIR

    tarfile_name = "gabriel-control.tar.gz"
    with cd(HOME_DIR):
        filelist = [CONTROL_DIR]
        _tar_files(tarfile_name, filelist) 

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
        # moped
        filelist = COMMON_FILES + [APP_COMM_DIR] + ["./src/app/object_moped/"]
        _tar_files("gabriel-moped.tar.gz", filelist)

        # OCR (open source)
        filelist = COMMON_FILES + [APP_COMM_DIR] + ["./src/app/ocr/"]
        exclude_list = ["./src/app/ocr/results", "./src/app/ocr/glass-client"]
        _tar_files("gabriel-ocr.tar.gz", filelist, exclude_list=exclude_list)
        

        # STF
        filelist = COMMON_FILES + [APP_COMM_DIR] + ["./src/app/object_stf"]
        exclude_list = ["./src/app/object_stf/Indexer/pstf/env", 
                        "./src/app/object_stf/Indexer/pstf/svmnet",
                        "./src/app/object_stf/Indexer/pstf/visionnet",
                        "./src/app/object_stf/Indexer/pstf/build",
                        "./src/app/object_stf/Indexer/pstf/src/build",
                        "./src/app/object_stf/Indexer/pstf/dist",
                        "./src/app/object_stf/Indexer/pstf/src/python_stf.egg-info",
                        "./src/app/object_stf/Indexer/pstf/tagfiles",
                        "./src/app/object_stf/Indexer/pstf/resources"]
        _tar_files("gabriel-object_stf.tar.gz", filelist, exclude_list=exclude_list)

        '''
        # face recognition (Windows)
        filelist = ["./src/app/face_recognition/dist"]
        _tar_files("gabriel-face.zip", filelist, comp_tool="zip")

        # AR (Windows)
        filelist = ["./src/app/mar/dist"]
        _tar_files("gabriel-mar.zip", filelist, comp_tool="zip")
        '''

#packaging_apps()
