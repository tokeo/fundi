# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
A ```threading``` shim for the single-threaded WASI guest.

WASI has no threads, so the real ```threading``` module is absent. This shim
provides the names a framework touches at import time. The lock types are
no-ops -- correct in a guest that can never run a second thread -- and starting
an actual ```Thread``` raises a clear error rather than silently doing nothing.
"""


class Thread:
    """A non-startable thread: the WASI guest is single-threaded."""

    def __init__(self, *args, **kwargs):
        """Refuse construction with a clear reason."""
        raise RuntimeError('threading.Thread is not available in the wasi guest (no threads)')


class _NoopLock:
    """A lock that does nothing: there is no second thread to exclude."""

    def __enter__(self):
        """Enter the (uncontended) critical section."""
        return self

    def __exit__(self, *args):
        """Leave the section; never suppress an exception."""
        return False

    def acquire(self, *args, **kwargs):
        """Acquire is always immediately successful."""
        return True

    def release(self):
        """Release is a no-op."""
        pass


def Lock():
    """Return a no-op lock."""
    return _NoopLock()


def RLock():
    """Return a no-op reentrant lock."""
    return _NoopLock()


class local:
    """Thread-local storage degenerates to plain attributes with one thread."""

    pass


def current_thread():
    """There is only ever the main thread."""
    return None


def get_ident():
    """A constant identity: the single guest thread."""
    return 1


def _shutdown():
    """Interpreter shutdown hook: nothing to join in a single-threaded guest."""
    pass
