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
