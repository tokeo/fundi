"""
A ```multiprocessing``` shim for the WASI guest.

WASI has no processes, so the real ```multiprocessing``` module is absent. This
shim provides only the name a framework imports at module load
(```Process```); attempting to start one raises a clear error rather than
failing obscurely later.
"""


class Process:
    """A non-startable process: the WASI guest cannot fork or spawn."""

    def __init__(self, *args, **kwargs):
        """Refuse construction with a clear reason."""
        raise RuntimeError('multiprocessing.Process is not available in the wasi guest (no processes)')
