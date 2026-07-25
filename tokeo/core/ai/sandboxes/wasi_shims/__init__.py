# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
Minimal stdlib shims for the WASI guest.

WASI has no processes or threads, so ```multiprocessing``` and ```threading```
are absent from the WASI standard library. Some frameworks (e.g. cement) import
a couple of names from them at module load even when they never start a thread
or process. These shims provide exactly those names so such a framework can be
imported in the guest; anything that would need real concurrency raises a clear
error, and the lock/local types are no-ops -- which is correct in a guest that
is single-threaded by construction.

This package is mounted read-only ahead of the real stdlib by the wasm sandbox
when ```shim_wasi_stdlib``` is on (the default), so the trusted tool path can
rebuild a framework-backed tool inside the guest.

See ```WASM.md``` in the parent ```sandboxes``` directory for the full wasm
sandbox documentation, including the trust models and how these shims fit in.
"""
