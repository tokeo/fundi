"""
The guard base class: a positioned step in the agent-loop pipeline.

A guard is a step at one or more stages of the run, from the raw request to the
final result. At its stage it receives the running ```TokeoAiContext``` (the
whole state -- the trace and its typed views) and, where the stage works on one,
a reference to that object (the ```ChatResult``` of a model answer, the
```Invocation``` of a tool call). It may inspect and refine what it sees.

The base ```TokeoAiGuard``` is the master: it can act at every stage and may
change the state in place. The stages are the methods ```on_begin```,
```on_prompt```, ```on_answer```, ```on_call```, ```on_return```, ```on_close```.
A guard participates at a stage by
**overriding** that method; the base versions are no-ops, so an unoverridden
stage simply does not run. The handler finds participation by reflection (a
method that differs from the base), so there is no separate phase declaration.
"""

import copy

from cement.core.meta import MetaMixin

from tokeo.core.utils.dict import deep_merge


# the stage method names, as constants so call sites and config use a name, not
# a string literal; the GUARD_STAGE_ prefix makes a stage reference obvious
GUARD_STAGE_ON_BEGIN = 'on_begin'
GUARD_STAGE_ON_PROMPT = 'on_prompt'
GUARD_STAGE_ON_ANSWER = 'on_answer'
GUARD_STAGE_ON_CALL = 'on_call'
GUARD_STAGE_ON_RETURN = 'on_return'
GUARD_STAGE_ON_CLOSE = 'on_close'

# the value standing for "all the class's stages": the base view key in the
# per-stage config (the default that every stage falls back to) and, in a
# composition stage list, "run at all the class's stages". a value, never a
# name; single underscore like the sandbox ```_all``` keyword
GUARD_STAGE_ANY = '_any'

# the stages in chain order; the handler reflects over these to learn which
# stages a guard participates in (an overridden method = active)
GUARD_STAGES = (
    GUARD_STAGE_ON_BEGIN,
    GUARD_STAGE_ON_PROMPT,
    GUARD_STAGE_ON_ANSWER,
    GUARD_STAGE_ON_CALL,
    GUARD_STAGE_ON_RETURN,
    GUARD_STAGE_ON_CLOSE,
)


class TokeoAiGuard(MetaMixin):
    """
    Base class for guards: a positioned, state-refining step in the loop.

    The master guard. It may act at any stage and change the running state in
    place; the derivations we ship narrow themselves to a few stages (by
    overriding only those methods) and, later, to a return contract. A stage
    method receives the running ```TokeoAiContext``` and, for the answer and
    tool stages, a reference to the object in hand; the base methods are no-ops,
    so a guard runs only at the stages it overrides. Participation is found by
    reflection over the ```on_*``` methods (cached per guard, see ```has_stage```),
    so there is no phase field.

    Its class is resolved from the ```ai.guards``` item ```type``` (a built-in
    short name or a dotted path) and instantiated with the application and the
    item's ```options``` as Meta overrides by the ```app.ai``` handler. Like a
    provider, it holds no mutable per-call state.

    ## Do not derive from this class directly

    This is the *master* base. Write a guard by deriving from one of the guard
    *types* instead -- ```TokeoAiAuditGuard```, ```TokeoAiPolicyGuard```,
    ```TokeoAiRedactGuard```, ... -- not from ```TokeoAiGuard``` itself. The type
    you pick states the guard's *role* (observe, govern, mask) on the agent's
    guard list and on the trace, and it sets the contract your subclass is
    expected to keep (an audit guard observes and changes nothing; a policy
    guard may deny; a redact guard masks but never denies). Deriving straight
    from ```TokeoAiGuard``` makes a guard with no declared role -- avoid it. See
    each type's ```base.py``` for which one fits and what it expects.

    ## The stages: what each one hands you, and how to use it

    A guard participates in a stage by **overriding** that stage's method. Each
    method receives the running ```TokeoAiContext``` (always) and, at the answer,
    tool, and close stages, the one object in hand. What you may do with it
    differs per stage; the rules below hold for every guard type (a type only
    narrows *which* changes are appropriate -- e.g. an audit guard reads but
    does not change).

    - **```on_begin(ctx)```** -- once, on the raw incoming request, before the
        first model call. There is no single work object; the conversation is
        ```ctx.messages``` (the full list of incoming messages). Read it to
        inspect the request. To change it, either mutate ```ctx.messages``` in
        place (edit a message's content, ```append```/```pop``` a turn) and
        return ```None```, or return a *new* list to replace the whole
        conversation (a trimmed history, an injected system turn). A returned
        list must be messages (dicts); anything else fails loud.

    - **```on_prompt(ctx)```** -- before *each* model call, on the outgoing
        conversation. Same object and same rules as ```on_begin```:
        ```ctx.messages``` is what is about to be sent to the model. Runs again
        on every loop turn (after a tool result is appended), so it sees the
        conversation grow.

    - **```on_answer(ctx, result)```** -- after each model call, on that call's
        answer. ```result``` is a ```ChatResult``` (```result.text```,
        ```result.reasoning```, ```result.refusal```, ```result.tool_calls```,
        ```result.usage```, ```result.raw```). Read or reshape its fields in
        place and return ```None```, or return a new ```ChatResult``` to replace
        it. This is where a guard may, for example, strip or rewrite a pending
        ```tool_calls``` entry before it is executed.

    - **```on_call(ctx, invocation)```** -- before a tool runs, on the pending
        call. ```invocation``` is an ```Invocation```: ```invocation.name```,
        ```invocation.arguments``` (a dict, filled), ```invocation.parameters```
        (the tool's schema), ```invocation.decision```/```invocation.reason```.
        The result is *not* filled yet. Reshape ```arguments``` here (e.g. mask a
        secret-looking value); deny the call by setting
        ```invocation.decision = Invocation.DENY``` with a ```reason``` (the loop
        continues, the model sees the denial). Return ```None``` (mutate in
        place) or a new ```Invocation```.

    - **```on_return(ctx, invocation)```** -- after the tool ran, on the same
        ```Invocation```. Now ```invocation.result``` (a ```ToolResult``` with
        ```.text``` and ```.data```) or ```invocation.error``` is filled, and
        ```invocation.sandbox``` names where it ran. Reshape ```result.text```
        (mask, shorten) or inspect ```error```. Return ```None``` or a new
        ```Invocation```.

    - **```on_close(ctx, result)```** -- once, on the final answer of the whole
        run, after the loop. ```result``` is the final ```ChatResult```. Same
        shape and rules as ```on_answer```; this is the last chance to reshape
        what the caller receives.

    Across the object stages (answer, call, return, close) the convention is the
    same: returning ```None``` means "I refined in place (or did nothing)";
    returning a *new* object of the same kind replaces it. ```ctx``` also exposes
    the typed views of the run (```ctx.messages```, ```ctx.invocations```,
    ```ctx.results```) and ```ctx.userdata``` (the caller's opaque carry-through),
    available at every stage.

    ## Stopping the run: a raise breaks the whole chain

    There are two ways a guard refuses, and they are very different in reach:

    - **Soft denial** -- only at a tool call. Set
        ```invocation.decision = Invocation.DENY``` (with a ```reason```) at
        ```on_call```. This skips *that one* tool call; the loop continues, the
        remaining guards and stages still run, and the model is told the call was
        denied so it can react. Nothing else is affected.

    - **Hard abort** -- at any stage. ```raise``` a ```TokeoAiGuardError``` (or a
        typed subclass such as ```TokeoAiPolicyGuardError```). The handler does
        *not* catch guard exceptions, so the raise propagates out of ```chat```
        and **ends the whole run at once** -- the rest of the guard chain, the
        remaining loop turns, and the final answer are all abandoned. (The loop's
        own try/except around a tool's *execution* only turns a crashing tool
        into an error result; it does not catch a guard's raise.)

    Raise a hard abort when proceeding would be wrong, not merely unwanted: a
    required masking backend is unreachable and the run must not continue
    unmasked; a policy is violated in a way that must stop everything, not just
    skip one call; an external audit store the run depends on is down. Use a soft
    denial when only the single action is the problem and the model should be
    allowed to try something else.

    Use the *typed* error of your guard's family (```TokeoAiAuditGuardError```,
    ```TokeoAiPolicyGuardError```, ```TokeoAiRedactGuardError```) rather than the
    bare base, so a caller wrapping ```chat``` can catch one kind of abort
    specifically. A caller catches ```TokeoAiGuardError``` to handle any guard
    abort, or a typed subclass to handle one family; uncaught, it surfaces as the
    run's failure to whoever called ```chat```.

    """

    class Meta:
        """Guard meta-data."""

        # the configurable defaults, as one dict; per-guard options (and per-stage
        # overrides) overlay this, read at runtime via _config. empty here: the
        # base guard has no settings of its own
        config_defaults = {}

    def __init__(self, app, *args, **kw):
        """
        Initialize the guard.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiGuard, self).__init__(*args, **kw)
        self.app = app
        # the set of stages this guard participates in, filled lazily on the
        # first has_stage call (reflection once per guard, then cached)
        self._stages = None
        # the raw ai.guards[name] declaration, set by the handler after build
        # (the guard parses its own per-stage options out of it); None when a
        # guard is built without a declaration (e.g. directly in a test)
        self._declaration = None
        # the per-stage merged settings, built lazily by _config and cached;
        # a dict {stage: full settings dict} plus the '_any' default view
        self._stage_configs = None

    def _setup(self, app):
        """
        Set up the guard after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def _config(self, stage=None):
        """
        Return the effective settings for a stage, merged and complete.

        The view is built once, lazily, and cached. The default view
        (```_any```) is the guard's ```Meta.config_defaults``` overlaid with
        the declaration's top-level ```options```; each stage that carries an
        ```on_<stage>.options``` override gets its own view, the default deep-
        merged with that override (so every stage view is complete -- a partial
        override fills in the rest from the default, never a hole). A stage with
        no override, or no stage at all, returns the default view.

        ### Args

        - **stage** (str, optional): The stage name (e.g. ```on_call```); None,
            ```''``` or an unknown/non-overridden stage returns the default view

        ### Returns

        - **dict**: The complete settings for the stage (read with ```.get()```)

        """
        if self._stage_configs is None:
            self._stage_configs = self._build_stage_configs()
        if not stage or stage not in self._stage_configs:
            return self._stage_configs[GUARD_STAGE_ANY]
        return self._stage_configs[stage]

    def _build_stage_configs(self):
        """
        Build the per-stage settings views from Meta defaults and the declaration.

        The base (```_any```) is ```Meta.config_defaults``` deep-merged with the
        declaration's ```options```. For each of the six stages that carries an
        ```on_<stage>.options``` block in the declaration, a full view is built as
        the base deep-merged with that override (on a deepcopy of the base each
        time, so the shared base is never mutated). Stages without an override
        are not stored; they fall back to ```_any``` in ```_config```.

        **List settings are merged, not replaced.** Deep merge appends lists
        (the Tokeo config rule, same as the yaml config handler): a ```patterns```
        list in ```options``` extends the ```config_defaults``` list, and a
        ```patterns``` list in an ```on_<stage>``` override extends the ```_any```
        list. A guard that wants a stage to use a different list must account for
        this (e.g. keep the default list empty and add per stage).

        ### Returns

        - **dict**: ```{'_any': <default view>, <stage>: <full view>, ...}```

        """
        defaults = self._meta.config_defaults or {}
        declaration = self._declaration or {}
        # the default view: Meta defaults deep-merged with the declaration options
        # (deepcopy so the class-level config_defaults is never mutated)
        base = deep_merge(copy.deepcopy(defaults), copy.deepcopy(declaration.get('options') or {}))
        configs = {GUARD_STAGE_ANY: base}
        # a per-stage view only where the declaration carries an on_<stage> block;
        # the override deep-merges onto a copy of the base (lists append, dicts
        # merge, scalars replace -- the shared Tokeo config rule)
        for stage in GUARD_STAGES:
            override = (declaration.get(stage) or {}).get('options')
            if override:
                configs[stage] = deep_merge(copy.deepcopy(base), copy.deepcopy(override))
        return configs

    def on_begin(self, ctx):
        """
        Act on the raw incoming request, once before the loop.

        ### Args

        - **ctx** (TokeoAiContext): The running state; at this stage only the
            incoming messages are present yet

        """
        pass

    def on_prompt(self, ctx):
        """
        Act on the outgoing messages, before each model call.

        ### Args

        - **ctx** (TokeoAiContext): The running state; ```ctx.messages``` is the
            conversation about to be sent to the model

        """
        pass

    def on_answer(self, ctx, result):
        """
        Act on the raw model answer, after each model call.

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **result** (ChatResult): The model's latest answer (text and/or tool
            calls), already tracked -- a reference to the object on the trace

        """
        pass

    def on_call(self, ctx, invocation):
        """
        Act on a tool call before it executes.

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The tool call about to run, already
            tracked -- a reference to the object on the trace; setting
            ```decision```/```reason``` to ```deny``` blocks it

        """
        pass

    def on_return(self, ctx, invocation):
        """
        Act on a tool call after it executes.

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The completed call (the same object seen
            at ```on_call```); its ```result``` or ```error``` may be reshaped

        """
        pass

    def on_close(self, ctx, result):
        """
        Act on the final result, once after the loop.

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **result** (ChatResult): The final answer about to be returned

        """
        pass

    def has_stage(self, stage):
        """
        Whether this guard participates in a stage.

        A guard is active at a stage when it overrides that stage's method (the
        override differs from the base no-op). The set is computed once per guard
        by reflection and cached, so the loop can ask cheaply per stage.

        ### Args

        - **stage** (str): A stage name (one of the ```GUARD_STAGE_*``` values)

        ### Returns

        - **bool**: True when the guard overrides that stage's method

        """
        if self._stages is None:
            cls = type(self)
            self._stages = frozenset(name for name in GUARD_STAGES if getattr(cls, name) is not getattr(TokeoAiGuard, name))
        return stage in self._stages
