"""
Tokeo ai extension.

Wires the ai core into a Cement application: registers the built-in providers,
exposes the ```app.ai``` handler, and adds the ```ai``` command group for the
agentic and ai-facing side. An extension registers its own provider, tool,
agent, or guard via ```app.ai.register``` (for example in a ```post_setup``` hook).

The technical namespace and the command group are both ```ai``` (this module,
the ```tokeo.core.ai``` package, and the ```ai``` config section).

Every configured component is an item in the uniform form ```{type, options}```:
```type``` names the class (a built-in short name or a dotted path), ```options```
carries the component's own settings. Profiles add their documented top-level
params (purpose, tools, enabled) around that form.

```yaml
ai:
  defaults:
    profile: mock          # model used when a call names none
    agent: null           # no default agent: calls run lean unless one is named
  tools:
    calc:  { type: myapp.core.ai.tools.calc.TokeoAiCalcTool }
    notes: { type: myapp.core.ai.tools.notes.TokeoAiNotesTool }
  guards:
    audit: { type: audit }
    safe:
      type: policy
      options:
        deny: [shell]
  agents:
    audited:
      type: default
      options:
        tools:
          - notes          # combined with the profile's own tools (calc)
        guards:
          - safe           # the tool-call pipeline of this agent
          - audit
  profiles:
    mock:
      type: mock
      purpose: mocking
      tools:
        - calc
```

### Notes

: With no selector given, ```app.ai``` uses ```ai.defaults.profile```, which
    ships as the built-in ```mock``` profile, so ```ai ask``` answers out of
    the box without any model or server; there is no hard-coded code
    fallback.

"""

from cement import ex
from cement.core.meta import MetaMixin
from cement.core.foundation import SIGNALS
from cement.core.exc import CaughtSignal

import importlib
import shlex
from copy import deepcopy

# the interactive chat shell uses prompt_toolkit for a real line editor with
# session history and completion (the same building blocks the scheduler
# shell uses); prompt_toolkit is a base dependency, so the imports are
# top-level
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion

from tokeo.ext.argparse import Controller
from tokeo.core.utils.base import as_list
from tokeo.core.utils.json import json_dump, TokeoJsonUnknownNameEncoder
from tokeo.core.ai.utils import parse_model_params, TokeoJsonAiTraceEncoder
from tokeo.core.ai import (
    TokeoAiError,
    Invocation,
    ChatMessage,
    TokeoAiContext,
    TokeoAiFundiAgent,
    TokeoAiResult,
)
from tokeo.core.ai.governor import (
    GOVERNOR_STAGES,
    GOVERNOR_STAGE_ON_BEGIN,
    GOVERNOR_STAGE_ON_PROMPT,
    GOVERNOR_STAGE_ON_ANSWER,
    GOVERNOR_STAGE_ON_CALL,
    GOVERNOR_STAGE_ON_RETURN,
    GOVERNOR_STAGE_ON_CLOSE,
)
from tokeo.core.ai.config.governors import resolve_governors
from tokeo.core.ai.config.tools import resolve_tools
from tokeo.core.ai.config.sandboxes import sandbox_contains_tool, sandbox_for
from tokeo.core.ai.config.profiles import resolve_profile, resolve_agent_name, selectable_names
from tokeo.core.ai.config.providers import provider_type_of
from tokeo.core.ai.guards.audit import TokeoAiTraceAuditGuard
from tokeo.core.ai.guards.policy import TokeoAiToolPolicyGuard, TokeoAiDenyPolicyGuard, TokeoAiAbortPolicyGuard
from tokeo.core.ai.guards.validate import TokeoAiToolSchemaValidator
from tokeo.core.ai.guards.redact import TokeoAiRegexRedactGuard
from tokeo.core.ai.sandboxes.in_process import TokeoAiInProcessSandbox
from tokeo.core.ai.sandboxes.subprocess import TokeoAiSubprocessSandbox
from tokeo.core.ai.linter import TokeoAiLinter
from tokeo.core.ai.providers.mock import TokeoAiMockProvider
from tokeo.core.ai.providers.oai_compat import TokeoAiOaiCompatProvider


class TokeoAi(MetaMixin):
    """
    AI handler for Tokeo applications, reached through ```app.ai```.

    Resolves a profile from the ```ai``` config section (by name, or by a field
    such as ```model``` or ```purpose```) and hands the resolved profile to the
    selected provider. Holds no mutable per-call state, so it is safe to use
    from several threads at once (for example dramatiq workers or scheduler
    jobs).

    ### Notes

    : The handler is registered as ```ai``` and is reached through ```app.ai```.
        It is a thin dispatcher over the registered providers, not a wrapper
        around any provider's full surface.

    """

    class Meta:
        """Handler meta-data and configuration defaults."""

        # Unique identifier for this handler
        label = 'tokeo.ai'

        # Configuration section name in the application config
        config_section = 'ai'

        # Default configuration settings
        config_defaults = dict(
            # the app-wide base budgets for the loop. they are resolved in three
            # levels, nearest wins: a chat() call argument, then the selected
            # agent's option, then this ai-section base. an agent leaves its
            # budget None to fall through to here, so this is the single home of
            # the default numbers. max_steps caps the tool rounds of one request
            # (0 = unlimited); max_loops caps the consecutive rounds without one
            # successful call (0 = unlimited), so a model stuck on denied or
            # failing calls is stopped. set either at ai.max_steps / ai.max_loops
            max_steps=0,
            max_loops=3,
            # whether the run records its step history (the trace). True by
            # default; set ai.trace=False to skip building the trace -- the
            # typed caches still fill, so guards keep working, but result.trace
            # stays empty (and ai ask --trace has nothing to show). only safe
            # when no active guard reads the trace itself
            trace=True,
            # the default profile (model) used when a call names none;
            # the built-in mock profile lets a fresh app answer at once
            defaults=dict(profile='mock'),
            # named profiles; each binds a provider type to its details. the
            # built-in mock profile lets a fresh app answer without any setup.
            # core ships no tools, so the mock starts empty; a project adds
            # and activates its own tools on a profile or an agent
            profiles=dict(
                mock=dict(
                    type='mock',
                    model='mock',
                    purpose='mocking',
                ),
            ),
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the ai handler.

        Stores the application reference only; the configuration is merged in
        the ```_setup``` method once the framework has loaded it.

        ### Args

        - **app**: The Tokeo application instance
        - **args**: Positional arguments passed to the parent initializer

        ### Keyword Args

        - **kw**: Keyword arguments passed to the parent initializer

        """
        super(TokeoAi, self).__init__(*args, **kw)
        self.app = app
        # the ai component registry lives on the handler: kind -> {name: cls}.
        # built-ins register here at post_setup; a project or third-party class
        # is named by a dotted ```type``` in the config and imported on demand.
        self._registry = {}
        # the handler instantiates resolved classes with the application on
        # first use and caches the (stateless) instances here
        self._provider_objs = {}
        self._tool_objs = {}
        self._agent_objs = {}
        self._governor_objs = {}
        self._sandbox_objs = {}
        # an app-wide sandbox override set via ```set_sandbox```; when set it
        # replaces the agent's sandbox chain for every tool (a deliberate,
        # process-global choice, e.g. force everything into a container)
        self._sandbox_override = None
        # whether a run records its trace (read from config in _setup); the loop
        # passes it to each context it builds
        self._trace_enabled = True

    def _setup(self, app):
        """
        Set up the ai handler.

        Called by the framework after the configuration has been loaded.
        Merges the default configuration so the ```ai``` section always exists,
        then reads the section into the handler once. After this the
        operational methods work off these attributes and never read the
        configuration again.

        ### Args

        - **app**: The Tokeo application instance

        """
        self.app.config.merge({self._meta.config_section: self._meta.config_defaults}, override=False)
        # pull the ai configuration into the handler once, at setup time
        self._defaults = self._config('defaults', fallback={}) or {}
        self._profiles = self._config('profiles', fallback={}) or {}
        self._agents = self._config('agents', fallback={}) or {}
        # the three governor sections merge into one registry, keyed by name;
        # a name must be unique across guards/transformers/conductors, so a
        # collision is a hard config error (the linter also flags it early)
        self._governors = {}
        for section in ('guards', 'transformers', 'conductors'):
            for name, item in (self._config(section, fallback={}) or {}).items():
                if name in self._governors:
                    raise TokeoAiError(f'ai governor {name!r} is declared more than once across guards, transformers and conductors')
                self._governors[name] = item
        # whether runs record their trace; read once, passed to each context
        self._trace_enabled = bool(self._config('trace', fallback=True))
        # the ```ai.sandboxes``` map uses the uniform item form: a dict value is
        # a sandbox item ({type, tools, except, options}); there are no groups
        # here (a sandbox lists tool/group names under its own ```tools```)
        self._sandboxes = self._config('sandboxes', fallback={}) or {}
        # the ```ai.tools``` map uses the uniform form: a dict value is an item
        # ({type, options}), a list value is a named group of member names.
        # split it once into the two maps the loop works with
        self._tool_items, self._tool_groups = {}, {}
        for name, value in (self._config('tools', fallback={}) or {}).items():
            if isinstance(value, list):
                self._tool_groups[name] = list(value)
            elif isinstance(value, dict):
                self._tool_items[name] = value

    def _config(self, key, **kwargs):
        """
        Get a configuration value from the extension's config section.

        A simple wrapper around the application's ```config.get``` that uses the
        correct configuration section. Used only at setup time to read the
        configuration into the handler.

        ### Args

        - **key** (str): Configuration key to retrieve

        ### Keyword Args

        - **kwargs**: Additional arguments passed to ```config.get```

        ### Returns

        - **Any**: Configuration value for the specified key

        """
        return self.app.config.get(self._meta.config_section, key, **kwargs)

    def register(self, kind, name, cls):
        """
        Register a class under a short name within a kind.

        ### Args

        - **kind** (str): The component kind, e.g. ```provider``` or ```tool```
        - **name** (str): The short ```type``` name that selects this class
        - **cls** (type): The class; the handler instantiates it with the app

        """
        self._registry.setdefault(kind, {})[name] = cls

    def resolve(self, kind, type_value):
        """
        Resolve a config ```type``` to a class.

        A dotted ```type``` (one containing a ```.```) is imported on demand, so a
        project or third-party class needs no registration; a bare short name
        is looked up in the kind's registry (the built-ins tokeo ships).

        ### Args

        - **kind** (str): The component kind, e.g. ```provider``` or ```tool```
        - **type_value** (str): A short name or a dotted ```module.Class``` path

        ### Returns

        - **type**: The resolved class

        ### Raises

        - **TokeoAiError**: If a short name is unknown, or a dotted path cannot
            be imported

        """
        if not isinstance(type_value, str) or not type_value:
            raise TokeoAiError(f'ai {kind} "type" is missing or not a string')
        if '.' in type_value:
            module_path, _, attr = type_value.rpartition('.')
            if not module_path:
                raise TokeoAiError(f'ai {kind} type {type_value!r} is not a dotted path')
            try:
                return getattr(importlib.import_module(module_path), attr)
            except (ImportError, AttributeError) as err:
                raise TokeoAiError(f'cannot import ai {kind} type {type_value!r}: {err}')
        try:
            return self._registry[kind][type_value]
        except KeyError:
            known = ', '.join(sorted(self._registry.get(kind, {}))) or '(none)'
            raise TokeoAiError(f'unknown ai {kind} type {type_value!r}; known: {known}')

    def registry(self, kind=None):
        """
        Inspect the ai component registry through ```app.ai```.

        ### Args

        - **kind** (str | None): A single kind (```provider```, ```tool``` ...),
            or ```None``` for the whole registry

        ### Returns

        - **dict**: A deep copy; ```{name: class}``` for one kind, or
            ```{kind: {name: class}}``` for all kinds, so callers cannot mutate
            the registry (classes are atomic to ```deepcopy```, values stay
            shared)

        """
        if kind is None:
            return deepcopy(self._registry)
        return deepcopy(self._registry.get(kind, {}))

    def selectors(self):
        """
        The selectable names for the interactive chat shell.

        Used to build the chat completer, so typing ```--profile``` offers the
        profile names, ```--agent``` the agent names, and ```--model``` /
        ```--purpose``` the distinct values across the enabled profiles.

        ### Returns

        - **dict**: Lists under the keys ```profile```, ```agent```, ```model```,
            and ```purpose```

        """
        return selectable_names(self._profiles, self._agents.keys())

    def _resolve(self, profile=None, model=None, purpose=None):
        # pick the profile via the shared resolver (config.profiles): at most one
        # selector, else the configured default; no default at all raises
        return resolve_profile(self._profiles, self._defaults.get('profile'), profile=profile, model=model, purpose=purpose)

    def _provider(self, provider_type):
        # instantiate the registered provider class with the application once
        # and reuse it; providers are stateless, so a racing double build is
        # harmless and needs no lock
        obj = self._provider_objs.get(provider_type)
        if obj is None:
            obj = self.resolve('provider', provider_type)(self.app)
            obj._setup(self.app)
            self._provider_objs[provider_type] = obj
        return obj

    def _tool(self, name):
        # instantiate the tool configured under ```ai.tools[name]``` once and
        # reuse it; the item has the uniform form: ```type``` (a built-in short
        # name or a full dotted path) resolves to a class, built with the
        # application and the item's ```options``` as keyword arguments (the
        # keys override the tool's Meta defaults, like an agent or a guard).
        # the same statelessness argument as for providers applies
        obj = self._tool_objs.get(name)
        if obj is None:
            item = self._tool_items.get(name)
            if not item or not item.get('type'):
                raise TokeoAiError(f'ai tool {name!r} is not configured under ai.tools')
            settings = item.get('options') or {}
            obj = self.resolve('tool', item['type'])(self.app, **settings)
            # the object carries its configured name (the ```ai.tools``` key)
            obj._configured_name = name
            # WHY: a sandbox that runs the tool in another process (subprocess,
            # docker) rebuilds it there. the import path comes from the tool's
            # class itself (so a registry shortname in the config crosses the
            # boundary too); only the configured options must travel -- carry
            # them on the instance so both twins are built the same way (the
            # uniformity rule)
            obj._tokeo_parent_instance_options = dict(settings)
            obj._setup(self.app)
            self._tool_objs[name] = obj
        return obj

    def _resolve_tools(self, names):
        # expand the selection through the shared tool resolver: group names
        # (lists under ```ai.tools```) become their member items, recursively;
        # order preserved, duplicates dropped (see config.tools.resolve_tools)
        return resolve_tools(names, self._tool_groups)

    def _tool_specs(self, names):
        # build openai-style function specs from the tools' merged Meta;
        # unknown names are skipped, an empty or missing list yields no specs
        specs = []
        for name in names or []:
            try:
                tool = self._tool(name)
            except TokeoAiError:
                continue
            specs.append(
                dict(
                    type='function',
                    function=dict(
                        name=name,
                        description=tool._meta.description,
                        parameters=tool._meta.parameters,
                    ),
                )
            )
        return specs

    def _agent(self, name):
        # build the agent configured under ```ai.agents[name]``` once and reuse
        # it; the entry has the uniform item form: ```type``` (a built-in short
        # name or a dotted path) resolves to an agent class, built with the
        # application. the agent reads its own composition (tools, guards, ...)
        # from the declaration via _config
        obj = self._agent_objs.get(name)
        if obj is None:
            config = self._agents.get(name)
            if not isinstance(config, dict) or not config.get('type'):
                raise TokeoAiError(f'ai agent {name!r} is not configured under ai.agents')
            obj = self.resolve('agent', config['type'])(self.app)
            # hand the agent its raw ai.agents[name] declaration; the agent reads
            # its own options out of it (see agent._config)
            obj._declaration = config
            obj._setup(self.app)
            self._agent_objs[name] = obj
        return obj

    def _agent_or_default(self, agent, profile=None):
        # resolve the agent to run via the shared resolver (config.providers):
        # an explicit call argument wins, else the profile's stated ```agent```
        # (null opts out), else ```ai.defaults.agent```. None means no agent is
        # bound, and under the sandbox rules a tool call is then denied
        name = resolve_agent_name(agent, profile, self._defaults.get('agent'))
        return self._agent(name) if name is not None else None

    def _deny_set(self, agent_obj, profile, call_deny=None):
        # the resolved set of denied tools, shared by the spec-trimming and the
        # exec-time defence line so both always agree: the agent's deny, the
        # profile's deny, and the call's deny, each a single name or a group
        denied = set()
        if agent_obj is not None:
            denied |= set(self._resolve_tools(as_list(agent_obj._config('deny'))))
        denied |= set(self._resolve_tools(as_list((profile or {}).get('deny'))))
        denied |= set(self._resolve_tools(as_list(call_deny)))
        return denied

    def _tools_minus_deny(self, agent_obj, profile, call_deny=None):
        # the active tool set: the agent's tools minus the shared deny set. a
        # call can only narrow the set, never extend it -- there is no way to
        # add a tool the agent does not carry. tools resolve to concrete names
        # first (so a denied group removes all its members), order is kept
        active = self._resolve_tools(agent_obj._config('tools'))
        denied = self._deny_set(agent_obj, profile, call_deny)
        return [name for name in active if name not in denied]

    def _governor(self, identity):
        # build the guard for an identity (the name as written) once and
        # reuse it; guards hold no per-call state, so one cached instance is
        # fine. two forms: a dotted class at the point of use (a '.' in the
        # name) runs with class defaults, not configurable (no declaration); a
        # short name is built from its ai.guards declaration -- ```type``` (a
        # built-in short name or a dotted path) resolves to a class, built with
        # the application and the declaration's ```options``` as keyword
        # arguments (the keys override the guard's Meta defaults)
        obj = self._governor_objs.get(identity)
        if obj is None:
            if '.' in identity:
                # dotted class at the point of use: resolve and build with class
                # defaults; it carries no declaration, so no options/per-stage
                obj = self.resolve('governor', identity)(self.app)
                obj._setup(self.app)
            else:
                item = self._governors.get(identity)
                if not isinstance(item, dict) or not item.get('type'):
                    raise TokeoAiError(f'ai governor {identity!r} is not configured under ai guards, transformers or conductors')
                settings = item.get('options') or {}
                obj = self.resolve('governor', item['type'])(self.app, **settings)
                # hand the guard its raw ai.guards[name] declaration; the guard
                # parses its own per-stage options out of it (see guard._config)
                obj._declaration = item
                obj._setup(self.app)
            self._governor_objs[identity] = obj
        return obj

    def _stages_of(self, identity):
        # the stages a guard identity's class can do, by reflection (the guard's
        # own has_stage over its on_* methods). used by the composition resolver
        # to intersect a declared stage list with what the class implements
        governor = self._governor(identity)
        return [stage for stage in GOVERNOR_STAGES if governor.has_stage(stage)]

    def _resolve_governors(self, agent_obj):
        # resolve the agent's guard composition: expand chains, parse the
        # per-guard stage lists, union per identity with nearest-wins, apply
        # omit. returns GovernorConfigEntry list in first-appearance order. with no
        # agent (or none selected) there is no pipeline
        if agent_obj is None:
            return []
        return resolve_governors(
            self._governors,
            agent_obj._config('governors') or [],
            agent_obj._config('omit') or [],
            self._stages_of,
        )

    def _governors_by_stage(self, agent_obj):
        # build the per-stage running order: one ordered guard list per stage.
        # the stage is the fixed band; within a stage the guards run in agent
        # list order. each resolved entry (GovernorConfigEntry) carries the stages
        # it runs at -- the intersection of its declared composition stages
        # and what its class implements -- so a guard runs at a stage only
        # when both the composition AND the class include it. with no guards
        # every list is empty and the loop runs as before.
        #
        # built fresh each call on purpose: it runs once per chat() (not per
        # round), the guard instances are already cached in _governor_objs, and the
        # build is microseconds against an LLM call's hundreds of milliseconds
        entries = self._resolve_governors(agent_obj)
        return {stage: [self._governor(entry.identity) for entry in entries if stage in entry.stages] for stage in GOVERNOR_STAGES}

    def _sandbox(self, name):
        # build the sandbox configured under ```ai.sandboxes[name]``` once and
        # reuse it; the entry has the uniform item form: ```type``` (a built-in
        # short name or a dotted path) resolves to a sandbox class, built with
        # the application. the sandbox reads its own options from the declaration
        # via _config. like a provider it holds no per-call state, so one cached
        # instance is fine
        obj = self._sandbox_objs.get(name)
        if obj is None:
            item = self._sandboxes.get(name)
            if not isinstance(item, dict) or not item.get('type'):
                raise TokeoAiError(f'ai sandbox {name!r} is not configured under ai.sandboxes')
            obj = self.resolve('sandbox', item['type'])(self.app)
            # hand the sandbox its raw ai.sandboxes[name] declaration; it reads
            # its own options out of it (see sandbox._config)
            obj._declaration = item
            obj._setup(self.app)
            # the object carries its configured name (the ```ai.sandboxes``` key,
            # e.g. ```jailed```, ```wasm_untrusted```) so callers can record
            # WHERE a tool ran without threading the name alongside the object
            obj._configured_name = name
            self._sandbox_objs[name] = obj
        return obj

    def set_sandbox(self, name):
        """
        Force every tool into one sandbox for this process, or clear it.

        An app-wide override that replaces the agent's sandbox chain: a
        deliberate, global choice (for example, run everything in a container
        regardless of the agent). It does not touch the agent's hard ```deny```.

        ### Args

        - **name** (str | None): A configured sandbox name, or ```None``` to
            clear the override and return to the per-agent chain

        """
        # validate eagerly so a bad name fails here, not deep in a later call
        if name is not None:
            self._sandbox(name)
        self._sandbox_override = name

    def _sandbox_tools_contain(self, name, tool_name):
        # do the tools of sandbox ```name``` contain ```tool_name```? delegates
        # to the shared resolver (config.sandboxes), which knows ```_all``` and
        # ```except```; tool/group names are expanded through _resolve_tools
        return sandbox_contains_tool(self._sandboxes.get(name), tool_name, self._resolve_tools)

    def _sandbox_for(self, tool_name, agent_obj):
        # choose the sandbox a tool runs in. the app-wide override wins; else
        # walk the agent's ordered chain via the shared resolver and take the
        # first sandbox whose tools contain the tool. None means the chain is
        # exhausted, which the caller turns into a deny (deny-by-default)
        if self._sandbox_override is not None:
            return self._sandbox(self._sandbox_override)
        if agent_obj is None:
            return None
        name = sandbox_for(tool_name, agent_obj._config('sandboxes'), self._sandboxes.get, self._resolve_tools)
        return self._sandbox(name) if name is not None else None

    def _denies(self, tool_name, agent_obj, profile, call_deny=None):
        # the exec-time defence line: refuse a tool that is in the shared deny
        # set before any sandbox lookup -- the same set used to trim the specs,
        # so a model calling a carved-out tool is refused here too
        return tool_name in self._deny_set(agent_obj, profile, call_deny)

    def _exec_in_sandbox(self, tool_name, arguments, agent_obj, profile=None, call_deny=None, invocation=None):
        # the seam: resolve the tool, choose its sandbox, and run the call
        # through it. a hard ```deny``` or an exhausted chain raises, so the
        # caller records a denial; otherwise the chosen sandbox contains the
        # ```tool.exec```. when an invocation is passed, record WHERE it ran
        # (the configured sandbox name) so the trace and audit can show it
        if self._denies(tool_name, agent_obj, profile, call_deny):
            raise TokeoAiError(f'tool {tool_name!r} is denied')
        tool = self._tool(tool_name)
        sandbox = self._sandbox_for(tool_name, agent_obj)
        if sandbox is None:
            raise TokeoAiError(f'tool {tool_name!r} has no sandbox in the agent chain (denied)')
        if invocation is not None:
            invocation.sandbox = getattr(sandbox, '_configured_name', None)
        return sandbox.exec(tool, arguments or {})

    def _exec_governed(self, call, call_governors, return_governors, ctx, agent_obj, profile, call_deny=None):
        # run one tool call through the guard pipeline: the on_call guards may
        # deny it, the tool runs (inside its sandbox) unless denied, and the
        # on_return guards always run (so a denial is recorded too). the guards
        # receive the running context plus the invocation -- the per-call object
        # they work on, handed in (not fished from the trace)
        invocation = Invocation(id=call.id, name=call.name, arguments=dict(call.arguments or {}))
        # track it at creation (the loop is the origin), so it is in the
        # invocations cache and on the trace from the first stage on
        ctx.track(self, invocation)
        # attach the tool's declared schema so an on_call guard can validate
        # the arguments; an unknown tool leaves it None and still errors at
        # exec below, exactly as without guards
        try:
            invocation.parameters = self._tool(invocation.name)._meta.parameters
        except TokeoAiError:
            pass
        # each guard runs and its step is recorded through supersede: a guard
        # that returns a fresh copy supersedes the working invocation (changed
        # step), one that mutates in place or returns nothing leaves it (an
        # unchanged step -- the guard is still on the trace, attributable)
        for governor in call_governors:
            invocation = ctx.supersede(governor, governor.on_call(ctx, invocation), invocation, stage=GOVERNOR_STAGE_ON_CALL)
            if invocation.decision == Invocation.DENY:
                break
        if invocation.decision != Invocation.DENY:
            try:
                # the seam: the agent's sandbox chain contains the exec (a hard
                # deny or an exhausted chain raises and is recorded as an error)
                # the sandbox wraps a plain value into a ToolResult itself, so
                # the innermost layer owns the wrap and this is always a ToolResult
                # hand the whole per-turn scratchpad in; the seam creates and
                # picks this tool's slice only if the tool declares a key
                invocation.result = self._exec_in_sandbox(
                    invocation.name,
                    invocation.arguments,
                    agent_obj,
                    profile,
                    call_deny,
                    invocation,
                )
            except Exception as err:
                # the pipeline is resilient: a failing tool is recorded and the
                # loop continues, instead of crashing the whole call
                invocation.error = f'{type(err).__name__}: {err}'
        for governor in return_governors:
            invocation = ctx.supersede(governor, governor.on_return(ctx, invocation), invocation, stage=GOVERNOR_STAGE_ON_RETURN)
        # the text fed back to the model for this call; the invocation is
        # returned too, so the loop reads the outcome off the object it has
        if invocation.decision == Invocation.DENY:
            content = f'denied: {invocation.reason or "blocked by a guard"}'
        elif invocation.error is not None:
            # a sandbox-machinery error (timeout, transport, an exhausted chain)
            content = f'error: {invocation.error}'
        elif invocation.result and invocation.result.state.exception is not None:
            # a tool that raised: caught by the sandbox, carried in its state
            content = f'error: {invocation.result.state.exception}'
        else:
            content = invocation.result.value.as_str if invocation.result and invocation.result.value else ''
        return invocation, content

    def chat(
        self,
        messages,
        deny=None,
        profile=None,
        model=None,
        model_params=None,
        purpose=None,
        agent=None,
        max_steps=None,
        max_loops=None,
        userdata=None,
    ):
        """
        Run the agent loop and return the run's ```TokeoAiResult```.

        Resolves a profile (the model), then calls the provider. The active
        tools come from the agent (the composition root, which also sets the
        budgets); the call cannot add tools, only narrow them. While the model
        asks for tool calls, the activated tools are executed and their results
        are fed back, until the model answers. With no activated tool the loop
        degrades to a single, plain call.

        Two budgets bound the loop and abort it with an error when reached:
        ```max_steps``` caps the tool rounds of one request, ```max_loops``` caps
        the consecutive rounds without one successful call (a model stuck on
        denied or failing calls). ```0``` means unlimited.

        ### Args

        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **deny** (list | str | None): Tools or groups this call forbids, on
            top of the agent's and the profile's deny. A call may only narrow
            the agent's tool set, never extend it
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **model_params** (dict | None): Per-call model parameters (temperature,
            top_p, ...) that override the profile's ```model_params``` for this
            request only; passed to the provider, which spreads them into the
            request. Providers without a configurable model ignore them
        - **purpose** (str | None): Select the first enabled profile by purpose
        - **agent** (str | None): Select an agent by name; defaults to the
            configured ```ai.defaults.agent``` when one is set
        - **max_steps** (int | None): Maximum tool rounds, 0 for unlimited;
            defaults to the agent's budget, otherwise the framework default
        - **max_loops** (int | None): Maximum consecutive rounds without one
            successful call, 0 for unlimited; defaults to the agent's budget,
            otherwise the framework default
        - **userdata**: An opaque value carried through the run on the context,
            untouched by the framework, for a guard or confirm hook to read back
            the caller's own context; its content is the caller's concern

        ### Returns

        - **TokeoAiResult**: The run result -- the final answer (a
            ```ChatResult```, no pending tool calls), the trace, and the status

        ### Raises

        - **TokeoAiError**: If no profile resolves or it carries no ```type```,
            or when a budget aborts the execution

        """
        name, profile = self._resolve(profile=profile, model=model, purpose=purpose)
        provider_type = provider_type_of(name, profile)
        provider = self._provider(provider_type)
        # the agent is the composition root: it supplies the tools and the
        # guards and sets the budgets and the sandbox chain, while the profile
        # selects only the model. the active tools are the agent's tools, minus
        # the agent's deny, minus the profile's deny, minus this call's deny --
        # a call can only narrow the set, never extend it. so several profiles
        # (and calls) share one agent and each carve out a subset. the agent is
        # resolved call > profile.agent > defaults.agent
        agent_obj = self._agent_or_default(agent, profile)
        if agent_obj is not None:
            requested = self._tools_minus_deny(agent_obj, profile, deny)
        else:
            requested = []
        if max_steps is None:
            # the agent's own budgets win; without one (None) the handler's
            # base defaults (config_defaults max_steps / max_loops) apply
            budget = agent_obj._config('max_steps') if agent_obj is not None else None
            max_steps = budget if budget is not None else self._config('max_steps', fallback=0)
        if max_loops is None:
            budget = agent_obj._config('max_loops') if agent_obj is not None else None
            max_loops = budget if budget is not None else self._config('max_loops', fallback=3)
        specs = self._tool_specs(requested)
        # guards (selected on the agent) are positioned steps in the loop; each
        # participates at the stations whose method it overrides. partition once
        # the per-stage running order: each stage's ordered guard list. the loop
        # asks each stage for its list and runs the guards in that order
        by_stage = self._governors_by_stage(agent_obj)
        at_begin = by_stage[GOVERNOR_STAGE_ON_BEGIN]
        at_prompt = by_stage[GOVERNOR_STAGE_ON_PROMPT]
        at_answer = by_stage[GOVERNOR_STAGE_ON_ANSWER]
        at_call = by_stage[GOVERNOR_STAGE_ON_CALL]
        at_return = by_stage[GOVERNOR_STAGE_ON_RETURN]
        at_close = by_stage[GOVERNOR_STAGE_ON_CLOSE]
        # the guarded path runs when any guard touches a tool stage; the lean
        # path stays for an agent with no tool-stage guards (and no guards)
        tool_governed = bool(at_call or at_return)
        # the context manages the run: it tracks every object onto one trace and
        # keeps the typed views (messages, invocations, results) in step. the
        # incoming messages are tracked at construction, so ctx.messages is the
        # conversation history. the current result is a local, handed to the
        # stages and tracked -- not a context field
        ctx = TokeoAiContext(messages=messages, userdata=userdata, trace=self._trace_enabled)
        # on_begin: once, on the raw incoming request, before any model call.
        # begin/prompt act on the accumulated conversation (no single handed-in
        # work object), so they refine the messages: a guard may mutate
        # ctx.messages in place (returning None) or hand back a fresh
        # conversation, and refine_messages records the step either way
        for governor in at_begin:
            ctx.refine_messages(governor, governor.on_begin(ctx), stage=GOVERNOR_STAGE_ON_BEGIN)
        # on_prompt: before each model call, on the outgoing messages
        for governor in at_prompt:
            ctx.refine_messages(governor, governor.on_prompt(ctx), stage=GOVERNOR_STAGE_ON_PROMPT)
        result = ctx.track(self, provider.chat(profile, ctx.messages, tools=specs, model_params=model_params))
        # on_answer: after each model call, on the model answer -- the result is
        # the handed-in work object, so the step is recorded through supersede
        for governor in at_answer:
            result = ctx.supersede(governor, governor.on_answer(ctx, result), result, stage=GOVERNOR_STAGE_ON_ANSWER)
        while result.tool_calls:
            # max_steps caps the tool rounds of one request; 0 is unlimited.
            # reaching it aborts loudly: a silent empty answer hides the cause
            if max_steps and ctx.loopdata.steps >= max_steps:
                raise TokeoAiError(f'ai max_steps ({max_steps}) reached, execution aborted')
            ctx.track(self, ChatMessage(self._assistant_turn(result)))
            succeeded = False
            for call in result.tool_calls:
                if tool_governed:
                    # the invocation is tracked inside _exec_governed at creation
                    # and handed back, so the outcome is read off the object
                    invocation, content = self._exec_governed(call, at_call, at_return, ctx, agent_obj, profile, deny)
                    ok = (
                        invocation.decision != Invocation.DENY
                        and invocation.error is None
                        and (invocation.result is None or invocation.result.state.exception is None)
                    )
                else:
                    # the lean path is as resilient as the guard pipeline: an
                    # unknown or failing tool (or a deny/exhausted sandbox
                    # chain) becomes feedback the model may correct itself on,
                    # instead of an exception killing the whole loop
                    try:
                        tool_result = self._exec_in_sandbox(
                            call.name,
                            call.arguments or {},
                            agent_obj,
                            profile,
                            deny,
                        )
                        # the sandbox always returns a ToolResult; a tool that
                        # raised is carried in its state, not thrown here
                        if tool_result.state.exception is not None:
                            content = f'error: {tool_result.state.exception}'
                            ok = False
                        else:
                            content = tool_result.value.as_str if tool_result.value else ''
                            ok = True
                    except Exception as err:
                        content = f'error: {type(err).__name__}: {err}'
                        ok = False
                succeeded = succeeded or ok
                ctx.track(self, ChatMessage(role='tool', tool_call_id=call.id, content=content))
            ctx.loopdata.steps += 1
            # max_loops caps the consecutive rounds without one successful
            # call (every call denied or failing); 0 is unlimited. this stops
            # a model stuck repeating broken calls, while any successful call
            # resets the counter and lets honest work continue
            ctx.loopdata.failed_loops = 0 if succeeded else ctx.loopdata.failed_loops + 1
            if max_loops and ctx.loopdata.failed_loops >= max_loops:
                raise TokeoAiError(f'ai max_loops ({max_loops}) reached, execution aborted')
            # on_prompt again before the follow-up model call
            for governor in at_prompt:
                ctx.refine_messages(governor, governor.on_prompt(ctx), stage=GOVERNOR_STAGE_ON_PROMPT)
            result = ctx.track(self, provider.chat(profile, ctx.messages, tools=specs, model_params=model_params))
            # on_answer again after the follow-up model call
            for governor in at_answer:
                result = ctx.supersede(governor, governor.on_answer(ctx, result), result, stage=GOVERNOR_STAGE_ON_ANSWER)
        # on_close: once, on the final result, after the loop
        for governor in at_close:
            result = ctx.supersede(governor, governor.on_close(ctx, result), result, stage=GOVERNOR_STAGE_ON_CLOSE)
        # the run result: the final answer (a ChatResult), plus the run's
        # history and counters lifted off the context (which ends here). the
        # answer is just the model answer; the trace and status ride alongside
        return TokeoAiResult(answer=result, loopdata=ctx.loopdata, trace=ctx.trace)

    def _assistant_turn(self, result):
        # rebuild the assistant message that requested the tool calls, in the
        # openai shape, so a real provider keeps a valid conversation
        return {
            'role': 'assistant',
            'content': result.text,
            'tool_calls': [
                {
                    'id': call.id,
                    'type': 'function',
                    'function': {'name': call.name, 'arguments': json_dump(call.arguments or {}, encoder=TokeoJsonUnknownNameEncoder())},
                }
                for call in result.tool_calls
            ],
        }

    def ask(
        self,
        prompt,
        deny=None,
        profile=None,
        model=None,
        model_params=None,
        purpose=None,
        agent=None,
        userdata=None,
    ):
        """
        Send a single user prompt through the loop and return the reply text.

        ### Args

        - **prompt** (str): The user prompt
        - **deny** (list | str | None): Tools or groups this call forbids, on
            top of the agent's and the profile's deny. A call may only narrow
            the agent's tool set, never extend it
        - **profile** (str | None): Select a profile by name
        - **model** (str | None): Select the first enabled profile by model
        - **model_params** (dict | None): Per-call model parameters that override
            the profile's ```model_params``` for this request only
        - **purpose** (str | None): Select the first enabled profile by purpose
        - **agent** (str | None): Select an agent by name; defaults to the
            configured ```ai.defaults.agent``` when one is set
        - **userdata**: An opaque value carried through the run on the context,
            untouched by the framework, for a guard or confirm hook to read back
            the caller's own context; its content is the caller's concern

        ### Returns

        - **str**: The assistant text

        """
        messages = [{'role': 'user', 'content': prompt}]
        result = self.chat(
            messages,
            deny=deny,
            profile=profile,
            model=model,
            model_params=model_params,
            purpose=purpose,
            agent=agent,
            userdata=userdata,
        )
        return result.answer.text


class _ChatCompleter(Completer):
    """
    Completion for the interactive ai chat shell.

    Unlike a ```NestedCompleter``` (which only completes from the start of the
    line, against its first word), this completes the selector switches and
    their configured values *anywhere* in the line -- so they still pop up
    after some prompt text, e.g. ```the weekday of today --agent gua|```. It
    looks only at the word under the cursor and the word before it:

    - the word before the cursor is a switch (```--profile``` / ```--agent``` /
        ```--model``` / ```--purpose```) -> offer that switch's configured
        values from the running config
    - the word under the cursor starts with ```-``` -> offer the switch names
    - otherwise (ordinary prompt text) -> offer nothing, so completion never
        gets in the way of typing a normal request

    """

    def __init__(self, names, switches, extra_switches=None):
        # names: {'profile': [...], 'agent': [...], 'model': [...],
        # 'purpose': [...]} from the handler; switches: {'--profile':
        # 'profile', ...} mapping the flag to its names key. extra_switches:
        # flag names offered for completion but carrying a free value (no
        # configured names to suggest), such as --model_param
        self._names = names
        self._switches = switches
        self._extra_switches = list(extra_switches or [])

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()
        # split the line into "the word being typed" (current) and "the word
        # before it" (prev). when the cursor sits just after a space the
        # current word is empty and prev is the last whole word
        if text[-1:].isspace() or text == '':
            current = ''
            prev = words[-1] if words else ''
        else:
            current = words[-1] if words else ''
            prev = words[-2] if len(words) >= 2 else ''
        if prev in self._switches:
            # completing a value right after a switch: offer that switch's
            # configured names that match what is typed so far
            for name in self._names.get(self._switches[prev], []):
                if name.startswith(current):
                    yield Completion(name, start_position=-len(current))
        elif current.startswith('-'):
            # completing a switch name itself (selectors plus the free-value
            # switches such as --model_param)
            for switch in list(self._switches) + self._extra_switches:
                if switch.startswith(current):
                    yield Completion(switch, start_position=-len(current))


class AiController(Controller):
    """
    Ai command group for the agentic and ai-facing commands.

    """

    class Meta:
        label = 'ai'
        stacked_type = 'nested'
        stacked_on = 'base'
        description = 'talk to the configured model and run agentic tasks'
        help = 'ai and agentic commands'

    @ex(
        help='ask the configured model a single prompt',
        arguments=[
            (['prompt'], dict(help='the prompt text', nargs='*')),
            (['--profile'], dict(help='select an ai profile by name', dest='profile')),
            (['--model'], dict(help='select an ai profile by model', dest='model')),
            (['--purpose'], dict(help='select an ai profile by purpose', dest='purpose')),
            (['--agent'], dict(help='select an ai agent by name', dest='agent')),
            (
                ['--model_param'],
                dict(
                    help='per-call model parameter as key=value (repeatable; key=null removes it)',
                    action='append',
                    dest='model_param',
                ),
            ),
            (
                ['--trace'],
                dict(
                    help='print the result as json with the full trace (every step keeps its object)',
                    action='store_true',
                    dest='as_trace',
                ),
            ),
            (
                ['--trace-compact'],
                dict(
                    help='print the result as json with a compact trace (omit the object on unchanged steps)',
                    action='store_true',
                    dest='as_trace_compact',
                ),
            ),
        ],
    )
    def ask(self):
        # join the words back into a single prompt, so it can be given without
        # quotes (for example: ai ask calc 2 + 3)
        prompt = ' '.join(self.app.pargs.prompt or [])
        if not prompt:
            raise TokeoAiError('no prompt given; usage: ai ask "your question"')
        model_params = parse_model_params(self.app.pargs.model_param)
        result = self.app.ai.chat(
            [{'role': 'user', 'content': prompt}],
            profile=self.app.pargs.profile,
            model=self.app.pargs.model,
            purpose=self.app.pargs.purpose,
            agent=self.app.pargs.agent,
            model_params=model_params or None,
        )
        # --trace prints the whole result with the full trace; --trace-compact
        # prints it with a compact trace (unchanged steps drop their repeated
        # object); otherwise just the answer text
        if self.app.pargs.as_trace or self.app.pargs.as_trace_compact:
            self.app.print(json_dump(result, encoder=TokeoJsonAiTraceEncoder(compact=self.app.pargs.as_trace_compact), indent=2))
        else:
            self.app.print(result.answer.text)

    @ex(
        help='start an interactive, multi-turn chat session',
        arguments=[
            (['--profile'], dict(help='select an ai profile by name', dest='profile')),
            (['--model'], dict(help='select an ai profile by model', dest='model')),
            (['--purpose'], dict(help='select an ai profile by purpose', dest='purpose')),
            (['--agent'], dict(help='select an ai agent by name', dest='agent')),
            (
                ['--model_param'],
                dict(
                    help='per-call model parameter as key=value (repeatable; key=null removes it)',
                    action='append',
                    dest='model_param',
                ),
            ),
        ],
    )
    def chat(self):
        # keep the running conversation so each turn sees the earlier ones.
        #
        # the prompt is a prompt_toolkit line editor with a session-only
        # InMemoryHistory (up/down walk this run's prompts, ctrl-r searches
        # them, no persistence -- the history lives and dies with the
        # session) and a completer: typing "--" offers the four selector
        # switches, and after one the configured values follow. inline
        # --profile/--agent/--model/--purpose change the session for the
        # turns that follow; an empty line is a no-op, and the session ends
        # only on "exit"/"quit" or Ctrl-D.
        #
        # prompt_toolkit appends each accepted, non-empty line to the shared
        # history automatically, so the arrows work without manual
        # bookkeeping; AutoSuggestFromHistory also offers the previous
        # matching prompt as greyed-out ghost text while typing.
        session = {
            'profile': self.app.pargs.profile,
            'agent': self.app.pargs.agent,
            'model': self.app.pargs.model,
            'purpose': self.app.pargs.purpose,
            'model_params': parse_model_params(self.app.pargs.model_param),
        }
        messages = []
        completer = self._chat_completer()
        # one prompt session for the whole chat, reused across turns (it
        # carries the history, the selector completer, auto-suggest, and
        # history-prefix search) instead of being rebuilt every turn
        editor = self._chat_session(InMemoryHistory(), completer)
        self.app.print('ai chat - "--" for options, "exit"/"quit" or Ctrl-D to quit')
        # patch_stdout keeps any guard/audit log lines printed during a turn
        # from corrupting the live prompt line
        with patch_stdout(raw=True):
            while True:
                try:
                    line = editor.prompt('> ')
                    # an empty line is a no-op; only exit/quit or Ctrl-D end
                    if not line.strip():
                        continue
                    if line.strip() in ('exit', 'quit'):
                        break
                    # apply any inline selector switches (validated against
                    # the running config); keep the rest as this turn's prompt
                    text, changed, error = self._chat_switches(line, session)
                    if error:
                        # a bad --profile/--agent/--model/--purpose value:
                        # report it with the configured options and re-prompt
                        self.app.print(f'ai chat - {error}')
                        continue
                    text = text.strip()
                    if changed and not text:
                        # a pure switch line: confirm the new selection and
                        # wait for the next prompt, no model call
                        params = session['model_params']
                        shown = ' '.join(f'{k}={v}' for k, v in params.items()) if params else '-'
                        self.app.print(
                            'ai chat - '
                            f"profile={session['profile'] or '-'} "
                            f"agent={session['agent'] or '-'} "
                            f"model={session['model'] or '-'} "
                            f"purpose={session['purpose'] or '-'} "
                            f'model_params={shown}'
                        )
                        continue
                    if not text:
                        continue
                    messages.append({'role': 'user', 'content': text})
                    try:
                        result = self.app.ai.chat(
                            messages,
                            profile=session['profile'],
                            model=session['model'],
                            purpose=session['purpose'],
                            agent=session['agent'],
                            model_params=session['model_params'] or None,
                        )
                    except TokeoAiError as err:
                        # a selector or provider problem from the handler
                        # (for example an agent given on the command line that
                        # is not configured): show it, drop the unanswered
                        # turn, and keep the session alive
                        self.app.print(f'ai chat - {err}')
                        messages.pop()
                        continue
                    messages.append({'role': 'assistant', 'content': result.answer.text})
                    self.app.print(result.answer.text)
                except KeyboardInterrupt:
                    # ctrl-c clears the current line and re-prompts, like a
                    # shell -- it does not end the session
                    continue
                except EOFError:
                    # ctrl-d ends the session
                    break
                except CaughtSignal as err:
                    # allow shutdown by the configured signals; re-raise any
                    # other caught signal instead of swallowing it
                    if err.signum in SIGNALS:
                        break
                    raise
                except Exception as err:
                    # surface unexpected errors instead of hiding them; the
                    # shell stays alive for the next turn
                    self.app.print(f'ai chat - error: {err}')

    def _chat_session(self, history, completer):
        # the reusable prompt_toolkit session for the chat shell. complete-
        # while-typing must stay on so the selector menu pops up as you type;
        # note that enable_history_search would silently turn it off (they are
        # mutually exclusive in prompt_toolkit), so it is deliberately not set
        return PromptSession(
            history=history,
            completer=completer,
            auto_suggest=AutoSuggestFromHistory(),
            complete_while_typing=True,
        )

    # the inline switches the chat shell understands; each --flag sets the
    # matching selector for the rest of the session. profile/model/purpose
    # are mutually exclusive (the handler resolves a profile by exactly one
    # of them), so setting one clears the other two; agent is independent
    _CHAT_SWITCHES = {'--profile': 'profile', '--agent': 'agent', '--model': 'model', '--purpose': 'purpose'}
    _CHAT_EXCLUSIVE = ('profile', 'model', 'purpose')

    def _chat_completer(self):
        # a position-independent completer built from the running config, so
        # "--" offers the four switches and a switch then offers its values
        # no matter where in the line they are typed. --model_param is offered
        # as a free-value switch (its key=value has no configured names)
        return _ChatCompleter(self.app.ai.selectors(), self._CHAT_SWITCHES, ['--model_param'])

    def _chat_switches(self, line, session):
        # pull any "--flag value" pairs out of the line, validate each value
        # against the running config, and apply them to the session selectors
        # only if all are valid. returns (residual_prompt, changed, error);
        # a normal prompt has none of these tokens and passes through
        # untouched. validation here (plus the completer) is what stops a
        # typo such as "--agent guardedsss" from silently doing nothing.
        # --model_param is handled apart from the four selectors: it is
        # repeatable and carries a key=value (not a validated single value), so
        # it merges into the session's model_params (null/empty removes a key)
        try:
            tokens = shlex.split(line)
        except ValueError:
            # an unbalanced quote (an apostrophe in the prompt): fall back to
            # a naive split, used only to spot the switch tokens
            tokens = line.split()
        names = self.app.ai.selectors()
        pending = []
        param_pairs = []
        rest = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token == '--model_param' and index + 1 < len(tokens):
                param_pairs.append(tokens[index + 1])
                index += 2
                continue
            if token in self._CHAT_SWITCHES and index + 1 < len(tokens):
                key = self._CHAT_SWITCHES[token]
                value = tokens[index + 1]
                if value not in names[key]:
                    # reject unknown values with the configured options
                    options = ', '.join(names[key]) or '(none configured)'
                    return line, False, f'unknown {key} {value!r}; available: {options}'
                pending.append((key, value))
                index += 2
                continue
            rest.append(token)
            index += 1
        if not pending and not param_pairs:
            # nothing changed: return the original line so a normal prompt's
            # punctuation and spacing survive exactly
            return line, False, None
        # apply model_param changes by merging onto the running session params,
        # so a key set earlier survives and a null/empty value drops just that
        # key. a malformed pair (no "=") reports an error and changes nothing
        if param_pairs:
            merged = dict(session.get('model_params') or {})
            try:
                update = parse_model_params(param_pairs)
            except TokeoAiError as err:
                return line, False, str(err)
            for pair in param_pairs:
                key = pair.partition('=')[0].strip()
                if key in update:
                    merged[key] = update[key]
                else:
                    # null/empty: parse_model_params dropped it, so remove it
                    # from the running session params too
                    merged.pop(key, None)
            session['model_params'] = merged
        for key, value in pending:
            if key in self._CHAT_EXCLUSIVE:
                # keep the three profile selectors mutually exclusive
                for other in self._CHAT_EXCLUSIVE:
                    session[other] = None
            session[key] = value
        return ' '.join(rest), True, None

    @ex(
        help='check the ai configuration for typos and broken references',
        arguments=[
            (['--strict'], dict(help='treat warnings as failures too', action='store_true', dest='strict')),
        ],
    )
    def lint(self):
        # report every form and reference problem at once, each with its
        # ```ai.<section>.<name>``` path; exit non-zero on errors, and on
        # warnings too when --strict is given
        issues = TokeoAiLinter(self.app).lint()
        for issue in issues:
            self.app.print(f'{issue.level}: {issue.path}: {issue.message}')
        if not issues:
            self.app.print('ai config ok')
            return
        errors = [issue for issue in issues if issue.level == 'error']
        warnings = [issue for issue in issues if issue.level == 'warning']
        notes = [issue for issue in issues if issue.level == 'note']
        self.app.print(f'ai config: {len(errors)} error(s), {len(warnings)} warning(s), {len(notes)} note(s)')
        if errors or (self.app.pargs.strict and warnings):
            self.app.exit_code = 1


def ai_extend_app(app):
    """
    Cement post-setup hook: create ```app.ai``` and register the built-ins.

    Extends the application with the ai handler, registers the built-in
    providers on it, and sets it up, once every extension has been loaded and
    the configuration is available. A project or third-party provider/tool is
    not registered here; it is named by a dotted ```type``` in the config and
    imported on demand.

    ### Args

    - **app**: The application instance

    """
    app.extend('ai', TokeoAi(app))
    # built-in provider, available by short name without any configuration:
    # mock is the neutral test double the framework needs for its own loop.
    # core ships no tools and no domain models; a project names its own
    # tools and providers by a dotted ```type```
    app.ai.register('provider', 'mock', TokeoAiMockProvider)
    app.ai.register('provider', 'oai_compat', TokeoAiOaiCompatProvider)
    # built-in agent: the standard composition root, the ```fundi``` type (the
    # master that wields the tools); a project configures its agents under
    # ```ai.agents``` and selects one per call, via a profile, or defaults.agent
    app.ai.register('agent', 'fundi', TokeoAiFundiAgent)
    # built-in sandboxes: in_process (zero isolation, the catch-all with
    # ```tools: _all```) and subprocess (fault/resource isolation via the worker)
    app.ai.register('sandbox', 'in_process', TokeoAiInProcessSandbox)
    app.ai.register('sandbox', 'subprocess', TokeoAiSubprocessSandbox)
    # built-in guard: the ready-to-use trace audit, logging every step at every
    # stage so the whole chain is visible. the audit *type* (TokeoAiAuditGuard)
    # is not registered -- it is a base to derive from in python, not selected
    app.ai.register('governor', 'trace_audit', TokeoAiTraceAuditGuard)
    # built-in policy guards: the type (TokeoAiPolicyGuard) is not registered --
    # it is a base to derive from in python. the implementations: tool_policy
    # (allow/deny by tool name), and two debugging guards -- deny_policy (always
    # a soft denial at the tool stages) and abort_policy (always a hard stop)
    app.ai.register('governor', 'abort_policy', TokeoAiAbortPolicyGuard)
    app.ai.register('governor', 'deny_policy', TokeoAiDenyPolicyGuard)
    app.ai.register('governor', 'tool_policy', TokeoAiToolPolicyGuard)
    # built-in guard: the argument-schema check before a tool runs
    app.ai.register('governor', 'tool_schema_validate', TokeoAiToolSchemaValidator)
    # built-in guard: masks secret-looking spans by regex at the tool stages
    # (the redact *type* TokeoAiRedactGuard is a base to derive from, unregistered)
    app.ai.register('governor', 'regex_redact', TokeoAiRegexRedactGuard)
    app.ai._setup(app)


def ai_lint_on_run(app):
    """
    Cement pre-run hook: lint the ai configuration before an ai command.

    Runs inside ```app.run``` (unlike ```post_setup```), so a typo in the ai config
    surfaces as a clean error through the application's own handler instead of a
    traceback. It guards only ```ai``` commands and steps aside for ```ai lint```
    and ```--help```, so the report and the help text stay reachable.

    ### Args

    - **app**: The application instance

    ### Raises

    - **TokeoAiError**: If the configuration has an error-level problem; the
        message lists every issue (warnings included) and points at ```ai lint```

    """
    argv = list(app.argv or [])
    tokens = [arg for arg in argv if not arg.startswith('-')]
    # only guard ai commands; let --help and the lint command itself through
    if not tokens or tokens[0] != 'ai':
        return
    if '-h' in argv or '--help' in argv or (len(tokens) >= 2 and tokens[1] == 'lint'):
        return
    issues = TokeoAiLinter(app).lint()
    if any(issue.level == 'error' for issue in issues):
        report = '\n'.join(f'  {issue.level}: {issue.path}: {issue.message}' for issue in issues)
        raise TokeoAiError(f'invalid ai configuration (run "ai lint" for the full report):\n{report}')


def load(app):
    """
    Load the ai extension.

    ### Args

    - **app**: The application instance

    ### Notes

    - Registers a post_setup hook that creates ```app.ai```, registers the
        built-in providers on it, and sets it up once the configuration is
        available
    - Registers a pre_run hook that lints the ai configuration before an ai
        command, so a typo fails with a clean message rather than a traceback
    - A project or third-party provider/tool is named by a dotted ```type``` in
        the config and imported on demand, so it needs no registration and no
        entry in the application extensions

    """
    app.hook.register('post_setup', ai_extend_app)
    app.hook.register('pre_run', ai_lint_on_run)
    app.handler.register(AiController)
