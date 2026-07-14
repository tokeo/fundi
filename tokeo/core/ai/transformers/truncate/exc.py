"""
Exceptions for truncate transformers.

A leaf module importing only the base ```TokeoAiTransformerError```, so a truncate
transformer can raise ```TokeoAiTruncateTransformerError``` at the top level
without a circular import.
"""

from tokeo.core.ai.transformers.exc import TokeoAiTransformerError


class TokeoAiTruncateTransformerError(TokeoAiTransformerError):
    """
    Raised by a truncate transformer that needs to stop the run.

    A truncate transformer only shortens text and never denies; this exists so a
    derived truncate transformer that *does* need to abort (e.g. a required
    shortening step cannot be applied and the run must not carry the oversized
    payload on) has a typed error to raise, distinct from other governors' aborts.
    """
