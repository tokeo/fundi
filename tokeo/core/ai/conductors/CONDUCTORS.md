# Building a conductor

A conductor is a governor whose character is **directing** the run -- it steers
how the loop proceeds (ordering, redirecting, driving the flow at its stages)
rather than securing (the guard) or reshaping (the transformer). It shares the
whole governor mechanic -- the stages, the write contract, coherence, and
stopping by ```raise```. That mechanic is one guide: the
`tokeo.core.ai.governor.TokeoAiGovernor` class documentation. This page states only
what is specific to the **conductor role**.

## Character: directing the course

A conductor is there to shape the *course* of the run -- the flow, not a single
call: soft denial (```invocation.decision = DENY```) is the guard's character.
What a conductor does is determined by its implementation; the loop honours it
either way and names the governor that decided (see ```GOVERNORS.md```). Like
every governor it may ```raise``` ```TokeoAiConductorError``` to stop the run
hard when proceeding would be wrong.

The section-purity check binds section and class at config time: a ```type```
listed under ```ai.conductors``` must resolve to a ```TokeoAiConductor```
subclass -- the declaration says which character you are writing.

## Shipped types

None yet. The core provides the ```TokeoAiConductor``` role to derive from; a
conductor is written by deriving from it and naming it by its dotted class path in
the config under ```ai.conductors```.
