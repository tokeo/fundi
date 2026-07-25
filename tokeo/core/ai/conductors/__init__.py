# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
Conductor derivations: steer the run.

The base conductor exception ```TokeoAiConductorError``` is re-exported here, so
the short path ```from tokeo.core.ai.conductors import TokeoAiConductorError```
reaches it.

No ready-to-use conductor ships in the core yet; the shared mechanic is on the
`tokeo.core.ai.governor.TokeoAiGovernor` base class, the role contract is below.

.. include:: ./CONDUCTORS.md
"""

from tokeo.core.ai.conductors.exc import TokeoAiConductorError

__all__ = [
    'TokeoAiConductorError',
]
