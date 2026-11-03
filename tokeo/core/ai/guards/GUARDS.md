# Building a guard

A guard is a governor that checks a tool call and may deny it. It shares the whole
governor mechanic (the stages, what each stage hands it, the write contract for a
result-changing step, keeping the views coherent, and the hard abort) with the
transformer and the conductor; that mechanic is one guide, the
```tokeo.core.ai.governor.TokeoAiGovernor``` class documentation. This page states
only what is specific to the guard role.

## The one power to deny

A guard alone may refuse. It refuses in one of two ways, very different in reach.

- Soft denial: at ```on_call```, set ```invocation.decision = Invocation.DENY```
    with a ```reason```. This skips *that one* tool call; the loop continues and the
    model is told (```denied: <reason>```), so it can correct itself
    (deny-and-continue). Only a guard decides; a transformer or conductor that
    changes the decision ends the run with an error. A no stays a no: at
    ```on_call``` the governors after it are skipped (only a confirm still runs),
    at ```on_return``` they run for the record but cannot lift it.
- Hard abort: ```raise``` a typed ```TokeoAiGuardError``` at any stage. The loop
    does not catch it and the run ends at once (the shared stop-the-run mechanic on
    ```tokeo.core.ai.governor.TokeoAiGovernor```). Use the typed error of the
    guard's family so a caller can catch one kind. Raise only when proceeding would
    be wrong, not merely unwanted.

## Do not derive from ```TokeoAiGuard``` directly

Write a guard by deriving from one of the guard *types*, not from ```TokeoAiGuard```
itself. The type states the sub-role on the agent's governor list and on the trace,
and says what your subclass is *for*.

## The guard types

- audit (```TokeoAiAuditGuard```): observe only, read and log, change nothing.
    ```trace_audit``` ships ready to use.
- policy (```TokeoAiPolicyGuard```): decide, allow or deny a call by rule.
    ```tool_policy``` (allow/deny by name), ```deny_policy``` and ```abort_policy```
    ship ready.
- redact (```TokeoAiRedactGuard```): mask secret-looking content. A security
    guard: it may deny/abort when a leak cannot be closed. ```regex_redact``` ships
    ready. (Length capping without a security purpose is a *truncate transformer*,
    not a guard; see ```TokeoAiTransformer```.)
- validate (```TokeoAiValidateGuard```): check a call against a contract.
    ```tool_schema_validate``` (arguments against the tool's parameters) ships
    ready.
- confirm (```TokeoAiConfirmGuard```): have a person or a function confirm the
    state the guards reached. ```confirm``` ships ready: it asks the channels on
    the hook ```tokeo_ai_confirm```, records every answer and its notes, and
    never turns a no into a yes (CONFIG.md, section 4.6).

## Reading the notes of a confirm

A ```no_retry``` of a confirm runs the stage again with the person's note in
```ctx.notes```; the guards judge anew and may read it. A generated project
carries an example in ```core/ai/guards/words.py``` and the agent ```careful```:
the word filter denies a call whose arguments contain a listed word, and a note
```deny: world``` adds a word. The second question then shows the new state.

```python
class WordFilterGuard(TokeoAiPolicyGuard):
    def on_call(self, ctx, invocation):
        words = []
        for record in ctx.notes:
            note = (record.get('note') or '').strip()
            if note.lower().startswith('deny:'):
                words += [word.strip() for word in note[5:].split(',') if word.strip()]
        text = json.dumps(invocation.arguments).lower()
        hits = [word for word in words if word.lower() in text]
        if hits:
            invocation.decision = Invocation.DENY
            invocation.reason = f'contains {", ".join(map(repr, hits))}'
```
