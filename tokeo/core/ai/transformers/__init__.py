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
