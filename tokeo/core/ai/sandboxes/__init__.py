# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
Sandbox derivations and the generic per-call job runner.

The sandbox classes are re-exported here, so the short path
```tokeo.core.ai.sandboxes.TokeoAiSubprocessSandbox``` reaches each (instead of
the full ```tokeo.core.ai.sandboxes.subprocess.TokeoAiSubprocessSandbox```). No
cycle: a sandbox module imports the base from the ```tokeo.core.ai``` facade,
which is always fully loaded before this subpackage __init__ runs. The wasm
import is safe without the optional ```wasmtime``` extra -- that import is lazy
inside the run path, not at module load. ```_common``` and ```runner``` carry no
sandbox class and are not re-exported.
"""

from tokeo.core.ai.sandboxes.in_process import TokeoAiInProcessSandbox
from tokeo.core.ai.sandboxes.subprocess import TokeoAiSubprocessSandbox
from tokeo.core.ai.sandboxes.wasm import TokeoAiWasmSandbox

__all__ = [
    'TokeoAiInProcessSandbox',
    'TokeoAiSubprocessSandbox',
    'TokeoAiWasmSandbox',
]
