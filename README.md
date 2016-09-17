Gabriel: Cognitive Assistance System based on Google Glass
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
* Flask-RESTful==0.2.1
* opencv >=2.4 (optional)
* numpy (optional)

To install:

    sudo apt-get install gcc python-dev default-jre python-pip pssh python-psutil
    sudo pip install -r server/requirements.txt

If you want to save server received video for debugging, you'll also need opencv and numpy library:

    sudo apt-get install python-opencv
    sudo pip install numpy

Installation - Default networking interface.
-------------
If your default networking interface is not `eth0`,
the current method to configuring other interfaces is
to replace `eth0` occurrences in the following files.

+ `<gabriel-repo>/gabriel/lib/gabriel_REST_server`
+ `<gabriel-repo>/bin/gabriel-ucomm`


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
    $ cd <gabriel-repo>/bin
    $ ./gabriel-control
    INFO     Start RESTful API Server
    INFO     Start UPnP Server
    INFO     Start monitoring offload engines
    INFO     * Mobile server(<class 'mobile_server.MobileVideoHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9098)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Mobile server(<class 'mobile_server.MobileAccHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9099)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Mobile server(<class 'mobile_server.MobileResultHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9101)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Application server(<class 'app_server.VideoSensorHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 10101)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * Application server(<class 'app_server.AccSensorHandler'>) configuration
    INFO      - Open TCP Server at ('0.0.0.0', 10102)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    INFO     * UComm server configuration
    INFO      - Open TCP Server at ('0.0.0.0', 9090)
    INFO      - Disable nagle (No TCP delay)  : 1
    INFO     --------------------------------------------------
    ```

2. Run `ucomm server` from the binary directory.

    ```
    $ cd <gabriel-repo>/bin
    $ ./gabriel-ucomm
    INFO     execute : java -jar /home/krha/gabriel/src/control/lib/gabriel_upnp_client.jar
      ...
    INFO    Gabriel Server :
      ...
    INFO    connecting to x.x.x.x:9090
    INFO    * UCOMM server configuration
    INFO     - Open TCP Server at ('0.0.0.0', 10120)
    INFO     - Disable nagle (No TCP delay)  : 1
    INFO    --------------------------------------------------
    INFO    Start forwarding data
    ```

    If `ucomm server` is successfully connected to `control server`, you can see
    a log message __"INFO     User communication module is connected"__ at
    `control server`.

3. Run cognitive engines.

    Here is a sample cognitive engine which returns the word "dummy" for every received 
    frame from a mobile device.

    ```
    $ cd <gabriel-repo>/bin
    $ ./gabriel-proxy-sample.py
    Discovery Control VM
    INFO     execute : java -jar /home/ubuntu/gabriel/gabriel/lib/gabriel_upnp_client.jar
    INFO     Gabriel Server :
      ...
    INFO     Success to connect to (u'x.x.x.x', 10101)
    INFO     Start getting data from the server
    INFO     Start publishing data
    INFO     New connection is starting at 1404328176.629762
    processing: {u'sensor_type': u'mjepg', u'type': u'emulated', u'id': 6503}
    INFO     returning result: {"result": "dummy", "sensor_type": "mjepg", "type": "emulated", "id": 6503, "engine_id": "dummy"}
      ...
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
    $ cd <gabriel-repo>/bin
    $ ./gabriel-proxy-http-display/proxy.py
    ```
    
    This cognitive engine sets up an HTTP server to publish the received images.
	If all properly set, you should now be able to see the camera images from 
	mobile devices by connecting to `http://gabriel_ip:7070/index.html`
	using your browser.



Related research works
--------------------------

* [Towards Wearable Cognitive Assistance](http://dl.acm.org/citation.cfm?id=2594383)
