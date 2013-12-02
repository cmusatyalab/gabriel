#!/usr/bin/env python
"""Diamond predicate loader

This preamble can be placed at the start of a zipfile and will extract a
python source archive (PACKAGE.tar.gz), if necessary create a virtualenv
located at /tmp/tmp-$USERID/$(sha1sum source.tar.gz) and then installs
the python module and its dependencies. Finally, it calls the entry_point
that is named as the first argument in the context of the virtualenv.

Example usage:
    python stf-classifier.pred evaluate_classifier --help

"""
import errno
import hashlib
import os
import shutil
import subprocess
import tempfile
import virtualenv

PACKAGE = 'python-stf'

def make_user_tmpdir(system_tmp=None):
    """Create user specific temporary directory
    """
    if system_tmp is None:
        system_tmp = os.environ.get('TMPDIR', '/tmp')
    tmpdir = os.path.join(system_tmp, "tmp-%d" % os.getuid())
    try:
        os.mkdir(tmpdir, 0700)
    except OSError, error:
        if error.errno != errno.EEXIST:
            raise
    return tmpdir

def setup_virtualenv(source, tmp=None):
    """Setup source-specific virtualenv.
    """
    tmp = make_user_tmpdir(tmp)
    source_hash = hashlib.new('sha1', source).hexdigest()
    venv = os.path.join(tmp, source_hash)

    if not os.path.exists(venv):
        envdir = tempfile.mkdtemp(prefix='venv-', dir=tmp)
        virtualenv.create_environment(envdir, site_packages=True,
                                      never_download=True)

        source_file = os.path.join(envdir, 'source.tar.gz')
        with open(source_file, 'w') as f:
            f.write(source)

        pip_install = [ os.path.join(envdir, 'bin', 'pip'), 'install',
            '--download-cache', os.path.join(os.environ['HOME'], 'pip-cache') ]

        subprocess.call(pip_install + [ source_file ],
                        stdout=sys.stderr, stderr=subprocess.STDOUT)
        try:
            os.chmod(envdir, 0755)
            os.symlink(envdir, venv)
        except OSError, error:
            if error.errno != errno.EEXIST:
                raise
            shutil.rmtree(envdir)

    # activate virtualenv for this process
    activate = os.path.join(venv, 'bin', 'activate_this.py')
    execfile(activate, dict(__file__=activate))
    return venv

# Create PACKAGE specific virtualenv
if __name__ == '__main__':
    from cStringIO import StringIO
    import sys
    import zipfile

    try:
        archive = zipfile.ZipFile(sys.argv[0])
        data = archive.read("%s.zip" % PACKAGE)
        archive.close()
        archive = zipfile.ZipFile(StringIO(data))
    except KeyError:
        pass
    SOURCE = archive.read("%s.tar.gz" % PACKAGE)
    archive.close()

    setup_virtualenv(SOURCE, tmp='/tmp')

    # reload pkg_resources to adjust for new package search path
    import pkg_resources
    reload(pkg_resources)
    load = pkg_resources.load_entry_point

    if not sys.argv[1:]:
        print "usage: %s <entry_point> [args]\n\nWhere <entry_point> can be" % \
                sys.argv[0]
        dist = pkg_resources.get_distribution(PACKAGE)
        entries = dist.get_entry_map()['console_scripts']
        for entry in sorted(entries.keys()):
            print "\t", entry
        sys.exit(0)
    sys.argv.pop(0)

    try:
        ENTRY_POINT = load(PACKAGE, 'internal_scripts', sys.argv[0])
    except ImportError:
        ENTRY_POINT = load(PACKAGE, 'console_scripts', sys.argv[0])
    sys.exit(ENTRY_POINT())

