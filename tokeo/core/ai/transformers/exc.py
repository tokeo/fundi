"""
Exceptions for transformers.

A leaf module: it imports only ```TokeoAiError``` from the ai package's own leaf
```exc``` module, so any transformer module can import ```TokeoAiTransformerError```
at the top level without a circular import. Each transformer package adds its own
typed subclass in its own ```exc``` module, one level deep.
"""

from tokeo.core.ai.exc import TokeoAiError


class TokeoAiTransformerError(TokeoAiError):
    """
    Base for errors raised by a transformer.

    A transformer reshapes and never denies a call; it raises this (or a typed
    subclass) only to stop the run hard at its stage, when a required reshaping
    cannot be applied and proceeding would be wrong. Catch
    ```TokeoAiTransformerError``` to handle any transformer abort; catch a typed
    subclass to handle one kind.
    """
