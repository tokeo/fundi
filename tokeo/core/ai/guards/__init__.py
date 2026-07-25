# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
Guard derivations: one check per module, around one tool call.

The base guard exception ```TokeoAiGuardError``` is re-exported here, so the
short path ```from tokeo.core.ai.guards import TokeoAiGuardError``` reaches it.

The guard-role contract (the deny power, the guard types) is the included guide
below; the shared governor mechanic it builds on -- the stages, the write contract
for a result-changing step, coherence, the memory note -- is on the
`tokeo.core.ai.governor.TokeoAiGovernor` base class.

.. include:: ./GUARDS.md
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError

__all__ = [
    'TokeoAiGuardError',
]
