"""
The guard role: a governor whose character is securing by checking.

A guard is a governor (see ```TokeoAiGovernor``` for the shared mechanic: the
stages, override-to-participate, the per-stage config, reflection). It adds the
one power the other roles lack -- a **soft denial** at a tool call -- and is the
role whose job is to check and, where a call is not allowed, refuse it.

## Do not derive from this class directly

Write a guard by deriving from one of the guard *types* -- ```TokeoAiAuditGuard```,
```TokeoAiPolicyGuard```, ```TokeoAiRedactGuard```, ... -- not from
```TokeoAiGuard``` itself. The type states the guard's sub-role (observe, govern,
mask) on the agent's guard list and on the trace, and says what your subclass
is *for* (an audit guard observes; a policy guard denies; a redact guard
masks) -- the implementation decides. Deriving straight from
```TokeoAiGuard``` makes a guard with no declared type -- avoid it.

## Soft denial: skip one call

A guard may refuse a single tool call at ```on_call``` by setting
```invocation.decision = Invocation.DENY``` with a ```reason```. This skips
*that one* call; the loop continues, the remaining governors and stages still
run, and the model is told the call was denied so it can react. This soft denial
is the guard's own -- a transformer or conductor reshapes and steers but never
denies. For stopping the whole run, any governor may ```raise``` a
```TokeoAiGuardError``` (the hard abort described on ```TokeoAiGovernor```); use
the typed error of the guard's family so a caller can catch one kind specifically.
"""

from tokeo.core.ai.governor import TokeoAiGovernor


class TokeoAiGuard(TokeoAiGovernor):
    """
    The guard role: a governor whose character is securing by checking.

    A guard shares the whole governor mechanic; its character is checking: it
    inspects and refines like any governor, and denying is what it is *for*
    (```invocation.decision = Invocation.DENY``` at ```on_call```, with a
    ```reason```) to skip that one call while the loop continues. Derive from a
    guard *type* (audit, policy, redact), not from this class directly; see the
    module docstring.

    """
