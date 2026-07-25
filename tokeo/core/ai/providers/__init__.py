# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
Provider derivations: transports behind the common provider contract.

The provider classes are re-exported here, so the short path
```tokeo.core.ai.providers.TokeoAiMockProvider``` reaches each (instead of the
full ```tokeo.core.ai.providers.mock.TokeoAiMockProvider```). No cycle: a
provider module imports the base from the ```tokeo.core.ai``` facade, which is
always fully loaded before this subpackage __init__ runs.
"""

from tokeo.core.ai.providers.mock import TokeoAiMockProvider
from tokeo.core.ai.providers.oai_compat import TokeoAiOaiCompatProvider

__all__ = [
    'TokeoAiMockProvider',
    'TokeoAiOaiCompatProvider',
]
