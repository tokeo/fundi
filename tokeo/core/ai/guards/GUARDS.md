# Building a guard

A guard is a governor that **checks** a tool call and may **deny** it. It shares the
whole governor mechanic -- the stages, what each stage hands it, the write contract
for a result-changing step, keeping the views coherent, and the hard abort -- with
the transformer and the conductor; that mechanic is one guide, the
`tokeo.core.ai.governor.TokeoAiGovernor` class documentation. This page states only
what is specific to the **guard role**.

## The one power: deny

A guard alone may refuse. It refuses in one of two ways, very different in reach.

- **Soft denial** -- at ```on_call```, set ```invocation.decision = Invocation.DENY```
    with a ```reason```. This skips *that one* tool call; the loop continues and the
    model is told (```denied: <reason>```), so it can correct itself
    (deny-and-continue). Denying is the guard's character; the loop honours a deny
    from any governor and names the one that decided in the reason it feeds back.
- **Hard abort** -- ```raise``` a typed ```TokeoAiGuardError``` at any stage. The loop
    does not catch it and the run ends at once (the shared stop-the-run mechanic on
    `tokeo.core.ai.governor.TokeoAiGovernor`). Use the typed error of the guard's
    family so a caller can catch one kind. Raise only when proceeding would be wrong,
    not merely unwanted.

## Do not derive from ```TokeoAiGuard``` directly

Write a guard by deriving from one of the guard *types*, not from ```TokeoAiGuard```
itself. The type states the sub-role on the agent's governor list and on the trace,
and says what your subclass is *for*.

## The guard types

- **audit** (```TokeoAiAuditGuard```) -- observe only: read and log, change nothing.
    ```trace_audit``` ships ready to use.
- **policy** (```TokeoAiPolicyGuard```) -- decide: allow or deny a call by rule.
    ```tool_policy``` (allow/deny by name), ```deny_policy``` and ```abort_policy```
    ship ready.
- **redact** (```TokeoAiRedactGuard```) -- mask secret-looking content. A security
    guard: it may deny/abort when a leak cannot be closed. ```regex_redact``` ships
    ready. (Length capping without a security purpose is a *truncate transformer*,
    not a guard -- see `TokeoAiTransformer`.)
- **validate** (```TokeoAiValidateGuard```) -- check a call against a contract.
    ```tool_schema_validate``` (arguments against the tool's parameters) ships ready.
- **confirm** (```TokeoAiConfirmGuard```) -- gate a call on an out-of-band approval.
