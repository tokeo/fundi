# Building a conductor

A conductor is a governor whose character is directing the run: it steers how the
loop proceeds (ordering, redirecting, driving the flow at its stages) rather than
securing (the guard) or reshaping (the transformer). It shares the whole governor
mechanic: the stages, the write contract, coherence, and stopping by ```raise```.
That mechanic is one guide: the ```tokeo.core.ai.governor.TokeoAiGovernor``` class
documentation. This page states only what is specific to the conductor role.

## A conductor directs the course

A conductor is there to shape the *course* of the run, the flow, not a single
call: soft denial (```invocation.decision = DENY```) is the guard's character.
What a conductor does is determined by its implementation; the loop honours it
either way and names the governor that decided (see "Ordering and roles" in
```tokeo/core/ai/GOVERNORS.md```). Like
every governor it may ```raise``` ```TokeoAiConductorError``` to stop the run
hard when proceeding would be wrong.

The section-purity check binds section and class at config time: a ```type```
listed under ```ai.conductors``` must resolve to a ```TokeoAiConductor```
subclass; the declaration says which character you are writing.

## Shipped types

None yet. The core provides the ```TokeoAiConductor``` role to derive from; a
conductor is written by deriving from it and naming it by its class path
(```module:Class```) in the config under ```ai.conductors```.

## An example: the today conductor

A generated project carries a conductor in ```core/ai/conductors/today.py``` and
the agent ```dated``` that uses it: after the first model answer it adds a call
of the tool ```current```, unless the model calls it itself, so a calendar agent
always works with the real date.

```python
class TodayConductor(TokeoAiConductor):
    def on_answer(self, ctx, result):
        mark = f'{self.config_name}:today'
        if ctx.turndata.get(mark):
            return None
        ctx.turndata[mark] = True
        if any(call.name == 'current' for call in result.tool_calls or []):
            return None
        return add_tool_call(result, 'current', get_token_hex(4, 'inj_'))
```

The run of ```ai ask "welcher tag ist heute" --agent dated```:

```
the model answers without a tool          <- it would guess the date
the conductor adds current()              -> fundi runs it: 2026-11-03 ...
the model answers again, with the date
```

The added call runs only when the agent offers the tool, and like every call it
passes the guards and the sandbox: a conductor can do nothing the model could
not.

## Originating a tool call

A conductor can add a tool call the model never asked for. ```on_answer``` runs
after the model answer but before the calls execute, so a call added there runs
this round. Two free helpers in ```tokeo.core.ai.utils``` do the shaping, so
they need no context object to cling to; import what you use:

```python
from tokeo.core.ai.utils import add_tool_call, drop_tool_calls
from tokeo.core.utils.uid import get_token_hex
```

- ```add_tool_call(result, name, call_id, **arguments)```: append a call.
  The caller passes the id, e.g. ```get_token_hex(4, 'inj_')``` from
  ```tokeo.core.utils.uid```: random, prefixed to mark it code-originated. The
  helper needs no context.
- ```drop_tool_calls(result, name=None)```: remove one kind by name, or all
  with ```None```.

Both return a new ```ChatResult```; hand it back and ```supersede``` takes it.
From the two you get every strategy: append only (```add_tool_call```), full
replace (```drop_tool_calls(result)``` then ```add_tool_call```), targeted swap
(```drop_tool_calls(result, 'x')``` then ```add_tool_call(..., 'y')```), or
defensive append (```if not result.tool_calls: add_tool_call(...)```).

### Steer with tool calls

Do not try to steer a model by slipping a ```system``` message into the loop
mid-run: a weaker model folds a late system message away and keeps its own
answer (it repeats the instruction but does not act on it). An injected tool
call sidesteps this; it is an executed call, not text the model may ignore.

### Run once, then stop (idempotence)

.. note::

    The examples below call ```delegate```, a sub-run of another agent. It is
    planned and not a tool yet, so they show the shape only; the today
    conductor above runs as it is.

```on_answer``` runs every round, so an originating conductor needs a stop
condition, or it injects forever. Two documented ways; pick one:

A mark in turndata (clean for several conductors; each writes its own key):

```python
class FactCheckConductor(TokeoAiConductor):
    def on_answer(self, ctx, result):
        if ctx.turndata.get('factcheck'):
            return
        ctx.turndata['factcheck'] = True
        return add_tool_call(result, 'delegate', get_token_hex(4, 'inj_'),
                             prompt=f'verify: {result.text}')
```

Scan the invocations (no extra state; keyed on the tool name, so two
different conductors injecting the same tool would see each other's call):

```python
class FactCheckConductor(TokeoAiConductor):
    def on_answer(self, ctx, result):
        already = any(i.id.startswith('inj_') and i.name == 'delegate'
                      for i in ctx.invocations)
        if result.tool_calls or already:
            return
        return add_tool_call(result, 'delegate', get_token_hex(4, 'inj_'),
                             prompt=f'verify: {result.text}')
```
