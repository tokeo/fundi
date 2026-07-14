"""
The ```truncate``` transformer *type*.

```TokeoAiTruncateTransformer``` is the type for truncate transformers: a
transformer that caps over-long text so a large payload cannot blow the context
budget or flood the trace and log. It does nothing on its own -- it is the class
you derive from to write a truncate transformer, so the agent's governor list and
the trace can say "this is a truncate step" while your subclass decides what to
shorten and where.

No ready-to-use truncate implementation ships in the core: a generated project
carries one as an editable example (its own
```core/ai/transformers/truncate.py```), named by its full dotted class path in
the config. The core provides only this derivable type.
"""

from tokeo.core.ai import TokeoAiTransformer


class TokeoAiTruncateTransformer(TokeoAiTransformer):
    """
    The truncate transformer *type*: cap over-long text.

    A pass-through base that does nothing by itself. It exists so a transformer
    can be *typed* as a truncate transformer -- shortening the text the run
    carries -- without carrying any behaviour of its own. It is not registered
    under a config name on purpose, because it is meant to be *derived from* in
    Python, not selected as-is.

    Derive from it when you want a transformer that shortens the text the run
    carries on -- a long tool result, a large final answer -- keeping a head and
    marking what was cut. Like every transformer it reshapes and never denies: it
    shapes the text, it does not allow or deny a call.

    Always derive from this type (```TokeoAiTruncateTransformer```), not from
    ```TokeoAiTransformer```: it states the transformer's role -- shortening -- on
    the agent and the trace. For *what object each stage hands you and which text
    to cap* (```on_return``` the ```invocation.result.value.as_str``` of a
    completed tool call; ```on_close``` the final ```ChatResult.text``` of the
    whole run; ```on_answer``` a per-round ```ChatResult.text```), see the stage
    guide in the `tokeo.core.ai.governor.TokeoAiGovernor` class
    documentation -- the single reference
    for building a governor across its stations.

    A truncate transformer only shortens and does not stop the run. If shortening
    is *required* and cannot be applied -- the run must not carry an oversized
    payload on and the cap cannot be made -- raise
    ```TokeoAiTruncateTransformerError```. That raise is not caught by the loop:
    it ends the whole run at once (see the stop-the-run section on
    `tokeo.core.ai.governor.TokeoAiGovernor`). Raise only when proceeding
    oversized would be wrong;
    otherwise shorten and let the run continue.

    """

    class Meta:
        """Truncate transformer meta-data."""

        # no configurable settings; empty per the config_defaults rule. a
        # concrete truncate transformer declares its own (e.g. limit, marker)
        config_defaults = {}
