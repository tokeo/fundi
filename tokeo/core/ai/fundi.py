# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
Hands out the version of fundi, counted by cement like the build does.
"""

from cement.utils.version import get_version as cement_get_version

VERSION = (2, 2, 0, 'final', 0)


def get_version(version=VERSION):
    """
    Hand out the version as string, as cement counts it for the build.

    ### Args

    - **version** (tuple, optional): Major, minor, patch, release and serial.
        Defaults to ```VERSION```.

    ### Returns

    - **str**: The version, e.g. ```2.2.0```, or ```2.2.0b1``` for a beta

    """
    return cement_get_version(version)
