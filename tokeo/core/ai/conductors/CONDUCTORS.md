# Building a conductor

A conductor is a governor that **steers** the run -- it directs how the loop
proceeds (ordering, redirecting, driving the flow at its stages) rather than only
checking (the guard) or only reshaping (the transformer). It shares the whole
governor mechanic -- the stages, the write contract, coherence, and stopping
by ```raise```. That mechanic is one guide: the
`tokeo.core.ai.governor.TokeoAiGovernor` class documentation. This page states only
what is specific to the **conductor role**.

## The one contract: steer, do not soft-deny

A conductor shapes the *course* of the run. It does not use the guard's soft denial
of a single call (```invocation.decision = DENY```); it directs the flow. Like every
governor it may ```raise``` ```TokeoAiConductorError``` to stop the run hard when
proceeding would be wrong.

The section-purity check enforces the role at config time: a ```type``` listed
under ```ai.conductors``` must resolve to a ```TokeoAiConductor``` subclass.

## Shipped types

None yet. The core provides the ```TokeoAiConductor``` role to derive from; a
conductor is written by deriving from it and naming it by its dotted class path in
the config under ```ai.conductors```.
