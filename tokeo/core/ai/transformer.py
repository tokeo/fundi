"""
The transformer role: a governor whose character is reshaping.

A transformer is a governor (see ```TokeoAiGovernor``` for the shared mechanic:
the stages, override-to-participate, the per-stage config, reflection). It shares
that mechanic with the guard and the conductor; its character is
**reshaping**: it inspects and refines the running state at its stages -- mask,
rewrite, enrich, replace an object with a fresh one of the same kind. Securing
is the guard's character, directing the conductor's -- characters in thought,
not fences: what a governor does is determined by its implementation, and the
loop honours it (a deny is honoured from any role, and the trace and the
feedback name the governor that decided).

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

    A transformer shares the whole governor mechanic; its character is
    reshaping: it inspects and refines like any governor -- return ```None```
    to refine in place, or a fresh object of the same kind to replace it.
    Reshaping is what it is *for*; the implementation decides what it does.
    Derive from a transformer *type*, not from this class directly; see the
    module docstring.

    """
