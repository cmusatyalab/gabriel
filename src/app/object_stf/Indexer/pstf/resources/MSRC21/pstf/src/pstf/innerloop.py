#
# STF filter, a searchlet for the OpenDiamond platform
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

try:
    import pyximport
    pyximport.install()
except ImportError:
    pass
try:
    from .innerloop_cy import compute_leafimage, DataPoint, Candidate
except ImportError:
    print "failed to load Cython code, falling back on Numpy"
    from .innerloop_np import compute_leafimage
    from .innerloop_py import DataPoint, Candidate

