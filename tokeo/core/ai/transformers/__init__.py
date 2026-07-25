# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
Transformer derivations: governors whose character is reshaping.

The base transformer exception ```TokeoAiTransformerError``` is re-exported here
for the short import path ```tokeo.core.ai.transformers```.

The full reference for how a governor works across its stages (the write contract
for a result-changing step, keeping the views coherent, stopping by raise) is on
the `tokeo.core.ai.governor.TokeoAiGovernor` base class; a transformer adds
only its role contract.

.. include:: ./TRANSFORMERS.md
"""

from tokeo.core.ai.transformers.exc import TokeoAiTransformerError

__all__ = [
    'TokeoAiTransformerError',
]
