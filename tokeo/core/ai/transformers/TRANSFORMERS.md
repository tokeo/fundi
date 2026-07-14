# Building a transformer

A transformer is a governor that **reshapes** the run and **never denies**. It
shares the whole governor mechanic with the guard and the conductor -- the stages,
the write contract for a result-changing step, keeping the views coherent, and
stopping the run by ```raise```. That mechanic is one guide: the
`tokeo.core.ai.governor.TokeoAiGovernor` class documentation. This page states only
what is specific to the **transformer role**.

## The one contract: reshape, never deny

A transformer may inspect and refine what a stage hands it -- mask, rewrite, enrich,
shorten -- and return either ```None``` (refined in place) or a fresh object of the
same kind (to replace it), exactly like any governor. What it must **not** do is
deny a call: it never sets ```invocation.decision = DENY```. Denial is the guard's
power; steering is the conductor's. A transformer only shapes what passes through.

The section-purity check enforces this at config time: a ```type``` listed
under ```ai.transformers``` must resolve to a ```TokeoAiTransformer``` subclass, so
a guard cannot slip into the transformer section.

## Stopping the run

A transformer does not soft-deny, but -- like every governor -- it
may ```raise``` ```TokeoAiTransformerError``` (or a typed subclass such
as ```TokeoAiTruncateTransformerError```) to stop the whole run hard, when a
*required* reshaping cannot be applied and proceeding would be wrong. The loop does
not catch it. Raise only when proceeding unshaped would be wrong; otherwise reshape
and let the run continue.

## The shipped type

- **truncate** (```TokeoAiTruncateTransformer```) -- caps over-long text so a large
  payload cannot blow the context budget or flood the trace. No ready-to-use
  implementation ships in the core; a generated project carries an editable
  example in its own ```core/ai/transformers/truncate.py```, named by its dotted
  class path in the config.
