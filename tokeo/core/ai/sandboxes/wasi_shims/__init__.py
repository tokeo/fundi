# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
A minimal stdlib shim for the WASI guest.

WASI has no processes, so ```multiprocessing``` is absent from the WASI
standard library. Some frameworks (e.g. cement) import a couple of names from it
at module load even when they never start a process. The shim provides exactly
those names so such a framework can be imported in the guest; anything that
would need a real process raises a clear error.

The wasm sandbox copies it ahead of the stdlib into the guest of the trusted
path when ```shim_wasi_stdlib``` is on (the default), so a framework-backed
tool can be rebuilt there. The untrusted path never gets it.

See ```WASM.md``` in the parent ```sandboxes``` directory for the full wasm
sandbox documentation, including the trust models and how these shims fit in.
"""
