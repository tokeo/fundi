# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

# cement is a build requirement, the build and the app count the version alike
from cement.utils.version import get_version as cement_get_version

VERSION = (2, 2, 0, 'final', 0)


def get_version(version=VERSION):
    return cement_get_version(version)
