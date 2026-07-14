"""
The ```truncate``` transformer package: the truncate transformer type and error.

```TokeoAiTruncateTransformer``` (in ```base```) is the empty type to derive from;
```TokeoAiTruncateTransformerError``` (in ```exc```) is the typed error a truncate
transformer raises to abort. Both are re-exported here, so the short path
```tokeo.core.ai.transformers.truncate``` reaches each.

No ready-to-use implementation ships here: a generated project carries an editable
example in its own ```core/ai/transformers/truncate.py```, derived from
```TokeoAiTruncateTransformer```.
"""

from tokeo.core.ai.transformers.truncate.exc import TokeoAiTruncateTransformerError
from tokeo.core.ai.transformers.truncate.base import TokeoAiTruncateTransformer

__all__ = [
    'TokeoAiTruncateTransformerError',
    'TokeoAiTruncateTransformer',
]
