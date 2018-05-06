Gabriel: Platform for Wearable Cognitive Assistance Applications [![PyPI](https://img.shields.io/pypi/v/elijah-gabriel.svg)](https://pypi.org/project/elijah-gabriel/) [![Docker Build Status](https://img.shields.io/docker/build/jamesjue/gabriel.svg)](https://hub.docker.com/r/jamesjue/gabriel) [![Docker Pulls](https://img.shields.io/docker/pulls/jamesjue/gabriel.svg)](https://hub.docker.com/r/jamesjue/gabriel/)
========================================================
Copyright (C) 2013-2014 Carnegie Mellon University
This is a developing project and some features might not be stable yet.
Please visit our website at [Elijah page](http://elijah.cs.cmu.edu/).



License
----------
All source code, documentation, and related artifacts associated with the
Gabriel open source project are licensed under the [Apache License, Version
2.0](http://www.apache.org/licenses/LICENSE-2.0.html).

A copy of this license is reproduced in the [LICENSE](LICENSE) file.


Applications
------------
We have built several applications on top of Gabriel with different wearable devices, including Google Glass and Microsoft HoloLens. Video demos for some of them can be found at http://goo.gl/02m0nL.


Installation - Gabriel System
-------------

Ensure the `python` executable on your PATH is Python 2
with `python --version`.
If you are using Python 3, setup and use a
[virtualenv][virtualenv] in an external directory
so that the `python` on your PATH is Python 2.

+ Initialize the virtual environment with `virtualenv -p python2.7 ~/.env-2.7`.
+ Use the environment with `source ~/.env-2.7/bin/activate`.
+ Stop using the environment with `deactivate`.

Replacing the symlink in a directory such as `/usr/bin/python`
is __not__ recommended because this can potentially break
other Python 3 applications.

You also need the following packages.

* parallel-ssh
* psutil >= 0.4.1
* JRE for UPnP
* six==1.1.0
* Flask==0.9
* Flask-RESTful==0.3.5
* opencv >=2.4 (optional)
* numpy (optional)

To install:

    sudo apt-get install gcc python-dev default-jre python-pip pssh python-psutil
    sudo pip install -r server/requirements.txt

If you want to save server received video for debugging, you'll also need opencv and numpy library:

    sudo apt-get install python-opencv
    sudo pip install numpy


Installation - Application
-------------

Described at README file of each application directory



Tested platforms
---------------------

We have tested at __Ubuntu 12.04 LTS 64-bit__ but it should work well other
version of Ubuntu with a current installation script. We expect this code also
works other Linux distributions as long as you can install required package.



How to use
--------------

1. Run the `control server` from the binary directory.

    ```
    $ cd <gabriel-repo>/server/bin
    $ ./gabriel-control
    INFO     Start RESTful API Server (port :8021)
    INFO     Start UPnP Server
    INFO     Start monitoring offload engines
    INFO     * Mobile server(<class 'gabriel.control.mobile_server.MobileControlHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 22222)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Mobile server(<class 'gabriel.control.mobile_server.MobileVideoHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9098)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Mobile server(<class 'gabriel.control.mobile_server.MobileAccHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9099)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Mobile server(<class 'gabriel.control.mobile_server.MobileAudioHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9100)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Mobile server(<class 'gabriel.control.mobile_server.MobileResultHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9111)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * UComm relay server(<class 'gabriel.control.ucomm_relay_server.UCommRelayHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9090)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Application server(<class 'gabriel.control.publish_server.VideoPublishHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 10101)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Application server(<class 'gabriel.control.publish_server.AccPublishHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 10102)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Application server(<class 'gabriel.control.publish_server.AudioPublishHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 10103)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    ```

    If your default networking interface is not `eth0` on the control server, you should use the `-n` parameter to pass the correct value. For example:

    ```
    $ ./gabriel-control -n eno1
    ```

    If you are using any of the provided Gabriel client that starts with "legacy", you should also enable legacy mode when running the Gabriel control, by passing the `-l` parameter.

    ```
    $ ./gabriel-control -l
    ```

2. Run `ucomm server` from the binary directory.

    ```
    $ cd <gabriel-repo>/server/bin
    $ ./gabriel-ucomm
    INFO     execute : java -jar /home/ubuntu/Workspace/gabriel/server/gabriel/lib/gabriel_upnp_client.jar
    INFO     Gabriel Server :
      ...
    INFO     {u'acc_tcp_streaming_ip': u'x.x.x.x',
     u'acc_tcp_streaming_port': 10102,
     u'audio_tcp_streaming_ip': u'x.x.x.x',
     u'audio_tcp_streaming_port': 10103,
     u'ucomm_relay_ip': u'x.x.x.x',
     u'ucomm_relay_port': 9090,
     u'ucomm_server_ip': None,
     u'ucomm_server_port': None,
     u'video_tcp_streaming_ip': u'x.x.x.x',
     u'video_tcp_streaming_port': 10101}
    INFO    connecting to x.x.x.x:9090
    INFO    * UCOMM server configuration
    INFO     - Open TCP Server at ('0.0.0.0', 10120)
    INFO     - Disable nagle (No TCP delay)  : 1
    INFO    --------------------------------------------------
    ```

    Gabriel by default uses UPnP to discover `control server` from the `ucomm server` and `cognitive engines`. 
    If this discovery protocol doesn't work in your case (possibly due to network settings), you can specify the IP address of `control server` directly.

    ```
    $ ./gabriel-ucomm -s x.x.x.x:8021
    ```

    Again, if your default networking interface is not `eth0` on the ucomm server, you should use the `-n` parameter to pass the correct value. For example:

    ```
    $ ./gabriel-ucomm -n eno1
    ```

    If `ucomm server` is successfully connected to `control server`, you can see
    a log message __"INFO     User communication module is connected"__ at
    `control server`.

3. Run cognitive engines.

    Here is a sample cognitive engine which returns the word "dummy" for every received 
    frame from a mobile device.

    ```
    $ cd <gabriel-repo>/server/bin/example-proxies/
    $ ./gabriel-proxy-dummy
    Discovery Control VM
    INFO     execute : java -jar /home/ubuntu/Workspace/gabriel/server/gabriel/lib/gabriel_upnp_client.jar
    INFO     Gabriel Server :
    INFO     {u'acc_tcp_streaming_ip': u'x.x.x.x',
     u'acc_tcp_streaming_port': 10102,
     u'audio_tcp_streaming_ip': u'x.x.x.x',
     u'audio_tcp_streaming_port': 10103,
     u'ucomm_relay_ip': u'x.x.x.x',
     u'ucomm_relay_port': 9090,
     u'ucomm_server_ip': u'x.x.x.x',
     u'ucomm_server_port': 10120,
     u'video_tcp_streaming_ip': u'x.x.x.x',
     u'video_tcp_streaming_port': 10101}
    TOKEN SIZE OF OFFLOADING ENGINE: 1
    ```

    Similarly, you can specify the IP address of `control server` through the `-s` parameter.

    ```
    $ ./gabriel-proxy-sample.py -s x.x.x.x:8021
    ```

    If `cognitive engine` is successfully connected to `ucomm server`, you can
    see a log message __"INFO    new Offlaoding Engine is connected"__ at
    `ucomm server`.

4. Run a mobile client using source code at `<gabriel-repo>/android/`. Make sure to
   change IP address of `GABRIEL_IP` variable at
   `src/edu/cmu/cs/gabriel/Const.java`.

5. HTTP display

    If you want to have a quick test of whether your image stream transmission 
    is working fine, you can run another cognitive engine by

    ```
    $ cd <gabriel-repo>/server/bin/example-proxies/gabriel-proxy-http-display
    $ ./proxy.py
    ```
    
    This cognitive engine sets up an HTTP server to publish the received images.
	If all properly set, you should now be able to see the camera images from 
	mobile devices by connecting to `http://gabriel_ip:7070/index.html`
	using your browser.



Related research works
--------------------------

* [Towards Wearable Cognitive Assistance](http://dl.acm.org/citation.cfm?id=2594383)
