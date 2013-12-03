#!/usr/bin/python
#
# STF trainer, trains an STF predicate for the OpenDiamond platform
#
# Copyright (c) 2011,2012 Carnegie Mellon University
#
# This filter is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, version 2.
#
# This filter is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License.
# If not, see <http://www.gnu.org/licenses/>.
#

import boto
from boto.exception import EC2ResponseError
from fabric.api import *
from fabric.main import load_settings
from fabric.network import disconnect_all
from fabric.tasks import WrappedCallableTask
import getpass
import math
import os
import re
import signal
import socket
import time

env.setdefault('ec2_access_key_id', 'UNSET')
env.setdefault('ec2_secret_access_key', 'UNSET')
env.setdefault('ec2_key_name', 'UNSET')

env.setdefault('ec2_zone', 'us-east-1a')
env.setdefault('ec2_instance_user', 'ubuntu')
env.setdefault('ec2_security_group', 'ssh-only')


# There is no API to get pricing, which varies by region in any event.
# This assumes us-east.  Also it does not account for any unused reserved
# instances.  Also it may be out of date.
INSTANCE_HOUR_PRICES = {
    't1.micro': .02,
    'm1.small': .08,
    'm1.medium': .16,
    'm1.large': .32,
    'm1.xlarge': .64,
    'm2.xlarge': .45,
    'm2.2xlarge': .90,
    'm2.4xlarge': 1.8,
    'c1.medium': .165,
    'c1.xlarge': .66,
}


# Ensure instances are terminated on SIGTERM or SIGHUP as well as SIGINT
def _handle_signal(_signum, _frame):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGHUP, _handle_signal)


class _InstanceCache(object):
    def __init__(self):
        self.__conn = None
        self._instances = {}  # (ami, type) -> instance
        self._hours = 0
        self._cost = 0.0

    def __del__(self):
        self.terminate_all()

    @property
    def _conn(self):
        # Defer creating the connection until the Fabric environment has
        # been fully populated
        if self.__conn is None:
            self.__conn = boto.connect_ec2(env.ec2_access_key_id,
                    env.ec2_secret_access_key)
        return self.__conn

    @property
    def _all_instances(self):
        return [i for ilist in self._instances.values() for i in ilist]

    def get(self, ami, type, count=1):
        all_matching = self._instances.setdefault((ami, type), [])
        all_available = [i for i in all_matching if not i.busy]
        new = count - len(all_available)
        if new > 0:
            # Launch new instances and add them to the cache
            print('Reusing %d %s instances, launching %d new ones...' % (
                    len(all_available), type, new))
            start_time = time.time()
            new_instances = self._conn.run_instances(ami,
                    min_count=new,
                    max_count=new,
                    key_name=env.ec2_key_name,
                    security_groups=[env.ec2_security_group],
                    instance_type=type,
                    placement=env.ec2_zone).instances
            all_matching.extend(new_instances)
            all_available.extend(new_instances)

            for instance in new_instances:
                # Add our attributes to object
                instance.start_time = start_time
                instance.busy = False

            for instance in new_instances:
                # Wait for the instance to be created; apparently this is not
                # done before the API call returns.
                while True:
                    try:
                        instance.update()
                    except EC2ResponseError:
                        time.sleep(0.5)
                    else:
                        break

                # Tag it
                instance.add_tag('Name', 'Training')
                instance.add_tag('Owner', '%s@%s' % (getpass.getuser(),
                        socket.gethostname()))

                # Wait for launch
                while instance.state == 'pending':
                    time.sleep(2)
                    instance.update()
                if instance.state != 'running':
                    raise Exception('Failed to launch instance')

                # To avoid caching SSH connections in the parent process, we
                # do not wait for boot here.  EC2Task does that in the child.
        else:
            print('Reusing %d %s instances' % (count, type))

        allocated = all_available[:count]
        for instance in allocated:
            assert not instance.busy
            instance.busy = True
        return allocated

    def release_all(self):
        for instance in self._all_instances:
            instance.busy = False

    def _terminate(self, instances):
        if not instances:
            return
        print('Terminating %d instances.' % len(instances))
        self._conn.terminate_instances([i.id for i in instances])
        for instance in instances:
            i_hours = math.ceil((time.time() - instance.start_time) / 3600.0)
            self._hours += i_hours
            self._cost += i_hours * INSTANCE_HOUR_PRICES.get(
                    instance.instance_type, float('NaN'))

    def terminate_idle(self):
        terminate = [i for i in self._all_instances if not i.busy]
        for k, ilist in self._instances.iteritems():
            self._instances[k] = [i for i in ilist if i.busy]
        self._terminate(terminate)

    def terminate_all(self):
        self._terminate(self._all_instances)
        self._instances = {}

    def shutdown(self):
        self.terminate_all()
        if not math.isnan(self._cost):
            print('%d instance hours, approx. $%.2f' % (self._hours,
                    self._cost))
        else:
            print('%d instance hours, cost unknown' % self._hours)


_instance_cache = _InstanceCache()


def ec2_instances(ami, type, count=1):
    return ['i_%s_%s_%d' % (ami, type, i) for i in range(count)]


class EC2Task(WrappedCallableTask):
    def get_hosts(self, *args, **kwargs):
        hosts = super(EC2Task, self).get_hosts(*args, **kwargs)
        instances = {}

        # Find out how many instances we need of each type
        for host in hosts:
            ami, type = self._parse_host(host)
            if ami:
                instances.setdefault((ami, type), 0)
                instances[(ami, type)] += 1

        # Launch or reuse the correct number of instances
        _instance_cache.release_all()
        for ami, type in instances.keys():
            instances[(ami, type)] = _instance_cache.get(ami, type,
                    instances[(ami, type)])

        # Terminate instances we're not going to use
        _instance_cache.terminate_idle()

        # Rewrite host list
        for i, host in enumerate(hosts):
            ami, type = self._parse_host(host)
            if ami:
                instance = instances[(ami, type)].pop(0)
                hosts[i] = '%d_%s_%s@%s' % (i, instance.id,
                        env.ec2_instance_user, instance.public_dns_name)
            else:
                hosts[i] = '%d__%s' % (i, host)
        return hosts

    def run(self, *args, **kwargs):
        i, instance_id, host = env.host_string.split('_', 2)
        with settings(index=int(i), instance=instance_id, host_string=host,
                disable_known_hosts=True):
            # Wait for the instance to finish booting
            puts('Waiting for instance...')
            with settings(connection_attempts=30, timeout=10):
                with hide('running'):
                    run('true')

            # Run the task
            super(EC2Task, self).run(*args, **kwargs)

    @staticmethod
    def _parse_host(host):
        match = re.match('^i_([^_]+)_([^_]+)_[0-9]+$', host)
        if match:
            return match.groups()
        else:
            return None, None


# Decorator that makes the decorated function into a task that can be run
# in parallel on Amazon EC2.  env.index is set to a unique integer index
# [0, n) for each task instance.  The host list should be generated by
# ec2_instances().
#
# Be careful when using this for short-running tasks, since the minimum
# billing increment is one instance-hour.
#
# This MUST be the outer (top) decorator on a task function, due to the
# behavior of fabric.decorators._wrap_as_new().
def ec2_task(f):
    return EC2Task(parallel(f))


def terminate_all():
    _instance_cache.terminate_all()


def shutdown():
    _instance_cache.shutdown()


# Base AMI on EBS-backed Oneiric AMD64
@ec2_task
@hosts(ec2_instances('ami-baba68d3', 'm1.small'))
def build_ami():
    # Apply updates
    sudo('apt-get update')
    sudo('apt-get -y upgrade')

    # Apparently the upgraded apt has an incompatible on-disk index format
    with settings(warn_only=True):
        sudo('apt-get update')

    # Install packages we need
    sudo('apt-get install -y python-argparse python-imaging python-libsvm ' +
            'python-pip python-virtualenv python-yaml unzip')

    sudo('apt-get build-dep -y python-numpy')
    sudo('pip install numpy')

    # Delete cache
    sudo('apt-get clean')

    # Clear out existing SSH keys, since per-instance initialization only
    # adds keys
    run('rm ~/.ssh/authorized_keys')

    # Stop the instance
    puts('Stopping instance...')
    conn = boto.connect_ec2(env.ec2_access_key_id, env.ec2_secret_access_key)
    instance = conn.get_all_instances([env.instance])[0].instances[0]
    instance.stop()
    while instance.state != 'stopped':
        time.sleep(2)
        instance.update()

    # Create an AMI
    puts('Creating AMI...')
    timestamp = time.time()
    image_id = conn.create_image(instance.id, 'training-%s' %
            time.strftime('%Y%m%d%H%M%S', time.localtime(timestamp)))
    while True:
        try:
            image = conn.get_image(image_id)
        except EC2ResponseError:
            time.sleep(0.5)
        else:
            break
    image.add_tag('Name', 'Training')
    image.add_tag('Creator', instance.tags['Owner'])
    image.add_tag('Creation Time', time.ctime(timestamp))
    while image.state == 'pending':
        time.sleep(2)
        image.update()
    if image.state != 'available':
        raise Exception('Failed to create AMI')

    puts('Created new AMI: %s' % image_id)


@ec2_task
@hosts(ec2_instances('ami-5f3be236', 't1.micro'))
def test_task():
    run('ifconfig eth0|grep "inet addr"')


if __name__ == '__main__':
    import sys
    try:
        env.update(load_settings(os.path.expanduser('~/.stfrc')))
        execute(globals()[sys.argv[1]])
    finally:
        disconnect_all()
        shutdown()
