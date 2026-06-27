"""
The agent base class and the standard fundi agent. An agent is the
composition root (tools, guards, sandboxes, deny, budgets); the loop
itself lives in the app.ai handler.
"""

import copy

from cement.core.meta import MetaMixin

from tokeo.core.utils.dict import deep_merge


class TokeoAiAgent(MetaMixin):
    """
    Declarative base class for agents, the composition root of an ai call.

    An agent binds the building blocks of a task together: which tools are
    active, which guards wrap each call, which sandboxes contain execution,
    and how many model calls the loop may take. The model itself is not part
    of the agent; it is bound late through the selected profile, so the same
    agent can run against the mock, a local model, or a hosted one. The class
    is resolved from the ```ai.agents``` item ```type``` (a built-in short name or
    a dotted path) by the ```app.ai``` handler, which passes the agent's
    configuration entry as keyword arguments.

    This base is declarative only: it carries the composition (```Meta```) and
    the lifecycle, and is not used directly. tokeo ships exactly one concrete
    agent, ```TokeoAiFundiAgent``` (the ```fundi``` type); a project may add its
    own by subclassing this. The agent loop itself lives in the ```app.ai```
    handler, not on the agent, so an agent only varies the composition, not
    the orchestration.

    ### Notes

    : ```Meta.config_defaults``` declares the configurable keys (```tools```,
        ```guards```, ```sandboxes```, ```deny```, ```max_steps```,
        ```max_loops```) with neutral defaults; the ```options``` of the
        ```ai.agents``` entry override them, and the effective value of each is
        read at runtime through ```_config```.

    """

    class Meta:
        """Agent meta-data."""

        # the configurable defaults, as one dict; the agent entry's options
        # overlay this, read at runtime via _config. tools: the tool selection
        # (item or group names), merged with the profile's. guards: the guard
        # selection (guard names) for the tool-call pipeline. sandboxes: the
        # ordered sandbox chain (sandbox names) -- a tool runs in the first
        # sandbox whose tools contain it; when none does the call is denied, so
        # an in_process sandbox with tools: _all placed last is the opt-in
        # catch-all. deny: tools (item or group names) forbidden outright before
        # any sandbox lookup, a hard exclusion unlike a sandbox except.
        # omit: guard identities to drop from this agent's composition (a local
        # leaving-out, e.g. one a chain brought in), next to guards so it
        # never collides with a guard or chain name.
        # max_steps: per-agent cap on tool rounds (0 = unlimited). max_loops:
        # per-agent cap on consecutive rounds without one successful call
        # (0 = unlimited). both resolve in three levels, nearest wins: a chat()
        # call argument, then this agent option, then the ai-section base
        # (ai.max_steps / ai.max_loops). None here means "this agent sets none"
        # -- the value falls through to the ai-section base, which is the home
        # of the default numbers
        config_defaults = dict(
            tools=[],
            guards=[],
            sandboxes=[],
            deny=[],
            omit=[],
            max_steps=None,
            max_loops=None,
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the agent.

        ### Args

        - **app**: The Tokeo application instance
        - **args**: Positional arguments for the parent initializer

        ### Keyword Args

        - **kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiAgent, self).__init__(*args, **kw)
        self.app = app
        # the raw ai.agents[name] declaration, set by the handler after build
        # (the agent reads its own options out of it); None when built without
        # a declaration (e.g. directly in a test)
        self._declaration = None
        # the effective composition (config_defaults overlaid with the
        # declaration's options), built lazily by _config and cached
        self._composition = None

    def _setup(self, app):
        """
        Set up the agent after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def _config(self, key, fallback=None):
        """
        Return the effective value of a composition key.

        Built once, lazily, and cached: ```Meta.config_defaults``` deep-merged
        with the declaration's ```options``` (the same Tokeo config rule as the
        yaml handler -- lists append, dicts merge, scalars replace). The agent
        has no stages, so there is one effective view, not a per-stage one.

        ### Args

        - **key** (str): The composition key (e.g. ```tools```, ```guards```)
        - **fallback** (any, optional): Returned when the key is absent

        ### Returns

        - **any**: The effective value for the key

        """
        if self._composition is None:
            defaults = self._meta.config_defaults or {}
            options = (self._declaration or {}).get('options') or {}
            # deepcopy so the class-level config_defaults is never mutated
            self._composition = deep_merge(copy.deepcopy(defaults), copy.deepcopy(options))
        return self._composition.get(key, fallback)


class TokeoAiFundiAgent(TokeoAiAgent):
    """
    The standard agent, registered as the ```fundi``` type.

    fundi (Swahili for master/craftsman) is the composition root that wields
    the tools: it inherits the declarative composition of ```TokeoAiAgent``` and
    is the one concrete agent tokeo ships. It adds no behaviour of its own --
    the loop lives in the ```app.ai``` handler, the agent only composes which
    tools, guards, and sandboxes that loop uses. A project that needs a
    different composition configures another ```ai.agents``` entry of this type;
    a project that needs a different orchestration subclasses ```TokeoAiAgent```.

    """

    pass
