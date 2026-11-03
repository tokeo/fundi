# Building a transformer

A transformer is a governor whose character is reshaping. It shares the whole
governor mechanic with the guard and the conductor: the stages, the write contract
for a result-changing step, keeping the views coherent, and stopping the run by
```raise```. That mechanic is one guide: the
```tokeo.core.ai.governor.TokeoAiGovernor``` class documentation. This page states
only what is specific to the transformer role.

## A transformer reshapes

The three roles describe characters in thought: a transformer
*reshapes*, a guard *secures*, a conductor *directs*. Only a guard
decides: when a transformer or conductor changes ```invocation.decision```, the
loop ends the run with a ```TokeoAiError``` that names it, a fault of the app.
When a guard denies without naming a reason, the loop stamps role and name of
the actor into the reason, so trace and feedback always show who decided.

A transformer may inspect and refine what a stage hands it (mask, rewrite, enrich,
shorten) and return either ```None``` (refined in place) or a fresh object of the
same kind (to replace it), exactly like any governor. What it must not do is
deny a call: it never sets ```invocation.decision = DENY```. Denial is the guard's
power; steering is the conductor's. A transformer only shapes what passes through.

The section-purity check binds the section to the class at config time: a
```type``` listed under ```ai.transformers``` must resolve to a
```TokeoAiTransformer``` subclass, so a guard cannot slip into the transformer
section. The behaviour is checked at runtime: a transformer that changes the
decision ends the run with an error.

## Stopping the run

A transformer does not soft-deny, but like every governor it
may ```raise``` ```TokeoAiTransformerError``` (or a typed subclass such
as ```TokeoAiTruncateTransformerError```) to stop the whole run hard, when a
*required* reshaping cannot be applied and proceeding would be wrong. The loop does
not catch it. Raise only when proceeding unshaped would be wrong; otherwise reshape
and let the run continue.

## The shipped type

- truncate (```TokeoAiTruncateTransformer```): caps over-long text so a large
  payload cannot blow the context budget or flood the trace. No ready-to-use
  implementation ships in the core; a generated project carries an editable
  example in its own ```core/ai/transformers/truncate.py```, named by its class
  path (```module:Class```) in the config.

## The input pipeline

```on_begin``` and ```on_prompt``` see the conversation before the model does:
```ctx.messages```, changed in place or replaced by a returned list. That is the
input pipeline: a transformer there enriches the prompt (a date, a retrieved
document, the instructions of a task), shortens a long history or rewrites a
request; a guard there may stop the run. ```on_begin``` runs once per run,
```on_prompt``` before each model call, so an enrichment that should happen once
keeps a mark in ```ctx.turndata``` under its own name.

Requests from outside (mail through smtpd, grpc, a web page) that call
```app.ai.ask``` or ```app.ai.chat``` run through the same stages.

A generated project carries an example in ```core/ai/transformers/today.py```: at
```on_prompt``` it puts today's date in front of the conversation, once per run.

```python
class TodayTransformer(TokeoAiTransformer):
    def on_prompt(self, ctx):
        mark = f'{self.config_name}:today'
        if ctx.turndata.get(mark):
            return
        ctx.turndata[mark] = True
        today = utc_now().date().isoformat()
        ctx.messages.insert(0, dict(role='system', content=f'Today is {today} (UTC).'))
```

A second example is a small RAG, in ```core/ai/transformers/retrieve.py``` and the
agent ```informed```: at ```on_prompt``` it ranks the sections of the files in
```docs/knowledge``` for the question by BM25 and puts the best ones, numbered with
their sources, before it. The same index serves the tool ```search_docs```, with
which the model looks up sections itself; then the retrieval is a tool call and
passes the guards like any other.
