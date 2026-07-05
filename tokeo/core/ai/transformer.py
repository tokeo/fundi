"""
The transformer role: a governor that regulates by reshaping, never denies.

A transformer is a governor (see ```TokeoAiGovernor``` for the shared mechanic:
the stages, override-to-participate, the per-stage config, reflection). It shares
that mechanic with the guard and the conductor and narrows the contract to
**reshaping**: it may inspect and refine the running state at its stages -- mask,
rewrite, enrich, replace an object with a fresh one of the same kind -- but it
never denies a tool call. Denial is the guard's power; steering is the
conductor's. A transformer only reshapes what passes through.

## Do not derive from this class directly

Write a transformer by deriving from one of the transformer *types*, not from
```TokeoAiTransformer``` itself. The type states the sub-role and the contract
your subclass keeps. Deriving straight from ```TokeoAiTransformer``` makes a
transformer with no declared type -- avoid it.
"""

from tokeo.core.ai.governor import TokeoAiGovernor


class TokeoAiTransformer(TokeoAiGovernor):
    """
    The transformer role: a governor that regulates by reshaping.

    A transformer shares the whole governor mechanic and narrows the contract to
    reshaping: it may inspect and refine like any governor -- return ```None```
    to refine in place, or a fresh object of the same kind to replace it -- but it
    never denies a call and never steers the run. Derive from a transformer
    *type*, not from this class directly; see the module docstring.

    """
