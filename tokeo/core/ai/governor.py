"""
The governor base class: a positioned, state-refining step in the loop.

A governor is the regulator layer over the tools and sandboxes: a step at one or
more stages of the run, from the raw request to the final result, that keeps the
run ordered and safe. At its stage it receives the running ```TokeoAiContext```
(the whole state -- the trace and its typed views) and, where the stage works on
one, a reference to that object (the ```ChatResult``` of a model answer, the
```Invocation``` of a tool call). It may inspect and refine what it sees.

The three roles built on it share this whole mechanic and differ in their
character: a ```guard``` secures by checking, a ```transformer``` reshapes,
a ```conductor``` directs the run. Those are characters in thought, not
fences -- everything a governor does is determined by its implementation,
and the loop honours it: a deny is honoured from any role, and the trace
and the feedback name the governor that decided.

A governor participates at a stage by **overriding** that stage's method; the
base versions are no-ops, so an unoverridden stage simply does not run. The
handler finds participation by reflection (a method that differs from the base),
so there is no separate phase declaration. Like a provider, a governor holds no
mutable per-call state.

The full reference for writing a governor -- the stages, what each hands you, the
write contract for a result-changing step, coherence and the memory note -- is the
included guide below. Each role adds only its own contract on top
(`TokeoAiGuard`, `TokeoAiTransformer`, `TokeoAiConductor`).

.. include:: ./GOVERNORS.md
"""

import copy

from cement.core.meta import MetaMixin

from tokeo.core.utils.dict import deep_merge
from tokeo.core.ai.exc import TokeoAiError


# the stage method names, as constants so call sites and config use a name, not
# a string literal; the GOVERNOR_STAGE_ prefix makes a stage reference obvious
GOVERNOR_STAGE_ON_BEGIN = 'on_begin'
GOVERNOR_STAGE_ON_PROMPT = 'on_prompt'
GOVERNOR_STAGE_ON_ANSWER = 'on_answer'
GOVERNOR_STAGE_ON_CALL = 'on_call'
GOVERNOR_STAGE_ON_RETURN = 'on_return'
GOVERNOR_STAGE_ON_CLOSE = 'on_close'

# the value standing for "all the class's stages": the base view key in the
# per-stage config (the default that every stage falls back to) and, in a
# composition stage list, "run at all the class's stages". a value, never a
# name; single underscore like the sandbox ```_all``` keyword
GOVERNOR_STAGE_ANY = '_any'

# the stages in chain order; the handler reflects over these to learn which
# stages a governor participates in (an overridden method = active)
GOVERNOR_STAGES = (
    GOVERNOR_STAGE_ON_BEGIN,
    GOVERNOR_STAGE_ON_PROMPT,
    GOVERNOR_STAGE_ON_ANSWER,
    GOVERNOR_STAGE_ON_CALL,
    GOVERNOR_STAGE_ON_RETURN,
    GOVERNOR_STAGE_ON_CLOSE,
)


class TokeoAiGovernor(MetaMixin):
    """
    Base class for governors: a positioned, state-refining step in the loop.

    The master governor. It may act at any stage and change the running state in
    place; the roles built on it (guard, transformer, conductor) narrow the
    contract, and the derivations we ship narrow themselves to a few stages (by
    overriding only those methods). A stage method receives the running
    ```TokeoAiContext``` and, for the answer and tool stages, a reference to the
    object in hand; the base methods are no-ops, so a governor runs only at the
    stages it overrides. Participation is found by reflection over the ```on_*```
    methods (cached per governor, see ```has_stage```), so there is no phase field.

    Its class is resolved from the role's registry item ```type``` (a built-in
    alias or a dotted path) and instantiated with the application by the
    ```app.ai``` handler, which then sets it up with its config name and its raw
    declaration -- setup builds the per-stage ```options``` views out of it
    once, and ```_config``` serves them. Like a provider, it holds no mutable
    per-call state.

    ## Do not derive from this class directly

    This is the *master* base. Write a governor by deriving from a role and, under
    it, from one of that role's *types* -- not from ```TokeoAiGovernor``` itself.
    The role states the character -- securing (guard), reshaping (transformer),
    directing (conductor) -- and the type states the sub-role your subclass is
    written for. Deriving straight from ```TokeoAiGovernor``` makes a step
    with no declared role -- avoid it.

    ## The stages: what each one hands you, and how to use it

    A governor participates in a stage by **overriding** that stage's method. Each
    method receives the running ```TokeoAiContext``` (always) and, at the answer,
    tool, and close stages, the one object in hand. What you may do with it differs
    per stage; the rules below hold for every role (the role says what a governor
    is *for* -- e.g. a transformer is there to reshape -- not what it can do).

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
        it. This is where a role may, for example, strip or rewrite a pending
        ```tool_calls``` entry before it is executed.

    - **```on_call(ctx, invocation)```** -- before a tool runs, on the pending
        call. ```invocation``` is an ```Invocation```: ```invocation.name```,
        ```invocation.arguments``` (a dict, filled), ```invocation.parameters```
        (the tool's schema), ```invocation.decision```/```invocation.reason```.
        The result is *not* filled yet. Reshape ```arguments``` here (e.g. mask a
        secret-looking value). Return ```None``` (mutate in place) or a new
        ```Invocation```. A guard may additionally deny the call here (see the
        guard role); other roles reshape but do not deny.

    - **```on_return(ctx, invocation)```** -- after the tool ran, on the same
        ```Invocation```. Now ```invocation.result``` (a ```ToolResult``` whose
        ```value``` is a ```ToolValue``` with ```as_str```, ```as_json``` and
        ```as_data```, plus a ```state``` carrying ```incomplete```,
        ```stdout```, ```stderr``` and ```exception```) is filled, or
        ```invocation.error``` holds a sandbox-machinery failure (a tool that
        raised is not an error here -- it rides in ```result.state.exception```).
        ```invocation.sandbox``` names where it ran. ```value``` is ```None```
        when the tool returned nothing, so guard the access. Reshape the result
        (mask, shorten) by writing the views you change, or replace the whole
        ```value``` via ```create_tool_result``` to keep the three views
        coherent. Return ```None``` or a new ```Invocation```.

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

    At any stage a governor may ```raise``` a ```TokeoAiGuardError``` (or a typed
    subclass). The handler does *not* catch these, so the raise propagates out of
    ```chat``` and **ends the whole run at once** -- the rest of the chain, the
    remaining loop turns, and the final answer are all abandoned. (The sandbox
    catches a tool that *raises* and records it in ```result.state.exception```;
    the loop's own try/except around the sandbox call only turns a machinery
    failure into ```invocation.error```. Neither catches a governor's raise.)
    Raise a hard abort when proceeding would be wrong, not merely unwanted. A
    guard has, in addition, a soft denial that skips only one call (see the guard
    role).

    """

    class Meta:
        """Governor meta-data."""

        # the configurable defaults, as one dict; per-item options (and per-stage
        # overrides) overlay this, read at runtime via _config. empty here: the
        # base governor has no settings of its own
        config_defaults = {}

    def __init__(self, app, *args, **kw):
        """
        Initialize the governor.

        ### Args

        - **app**: The Tokeo application instance
        - **args**: Positional arguments for the parent initializer

        ### Keyword Args

        - **kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiGovernor, self).__init__(*args, **kw)
        self.app = app
        # the set of stages this governor participates in, filled lazily on the
        # first has_stage call (reflection once per governor, then cached)
        self._stages = None
        self._config_name = None
        self._stage_config_options = None

    @property
    def config_name(self):
        """
        The name this governor answers to. Never ```None``` or empty.

        The declared config key -- or the dotted class as written at the
        point of use -- once setup handed one in, the dotted class
        (```module.Class```) until then.

        ### Returns

        - **str**: The declared key, or the dotted class

        """
        if self._config_name:
            return self._config_name
        return f'{type(self).__module__}.{type(self).__name__}'

    def _setup(self, app, config_name=None, config=None):
        """
        Set up the governor with its config.

        Called by the handler right after the build. An override must call
        ```super()._setup(...)``` first -- it builds the views ```_config```
        reads.

        ### Args

        - **app**: The Tokeo application instance
        - **config_name** (str, optional): The key the governor is declared
            under (```shredder```), or the dotted class at the point of use
        - **config** (dict, optional): The raw registry declaration

        """
        if config_name:
            self._config_name = config_name
        self._stage_config_options = self._build_stage_config_options(config or {})

    def _config(self, stage=None):
        """
        Return the effective settings for a stage, merged and complete.

        Reads the views setup built. The default view
        (```_any```) is the governor's ```Meta.config_defaults``` overlaid with
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

        ### Raises

        - **TokeoAiError**: If config options were not set by setup

        """
        if self._stage_config_options is None:
            raise TokeoAiError(f'{type(self).__name__}: config options were not set by setup')
        if not stage or stage not in self._stage_config_options:
            return self._stage_config_options[GOVERNOR_STAGE_ANY]
        return self._stage_config_options[stage]

    def _build_stage_config_options(self, declaration):
        """
        Build the per-stage settings views from config_defaults and a declaration.

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
        list. A governor that wants a stage to use a different list must account
        for this (e.g. keep the default list empty and add per stage).

        ### Args

        - **declaration** (dict): The raw registry item (```{}``` for none)

        ### Returns

        - **dict**: ```{'_any': <default view>, <stage>: <full view>, ...}```

        """
        defaults = self._meta.config_defaults or {}
        # the default view: config_defaults deep-merged with the declared options
        # (deepcopy so the class-level config_defaults is never mutated)
        base = deep_merge(copy.deepcopy(defaults), copy.deepcopy(declaration.get('options') or {}))
        configs = {GOVERNOR_STAGE_ANY: base}
        # a per-stage view only where the declaration carries an on_<stage> block;
        # the override deep-merges onto a copy of the base (lists append, dicts
        # merge, scalars replace -- the shared Tokeo config rule)
        for stage in GOVERNOR_STAGES:
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
            tracked -- a reference to the object on the trace; a guard may set
            ```decision```/```reason``` to ```deny``` to block it

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
        Whether this governor participates in a stage.

        A governor is active at a stage when it overrides that stage's method (the
        override differs from the base no-op). The set is computed once per
        governor by reflection and cached, so the loop can ask cheaply per stage.

        ### Args

        - **stage** (str): A stage name (one of the ```GOVERNOR_STAGE_*``` values)

        ### Returns

        - **bool**: True when the governor overrides that stage's method

        """
        if self._stages is None:
            cls = type(self)
            self._stages = frozenset(name for name in GOVERNOR_STAGES if getattr(cls, name) is not getattr(TokeoAiGovernor, name))
        return stage in self._stages
