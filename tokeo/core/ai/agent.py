"""
The agent base class and the standard fundi agent. An agent is the
composition root (tools, guards, sandboxes, deny, budgets); the loop
itself lives in the app.ai handler.
"""

import copy

from cement.core.meta import MetaMixin

from tokeo.core.utils.dict import deep_merge
from tokeo.core.ai.exc import TokeoAiError


class TokeoAiAgent(MetaMixin):
    """
    Declarative base class for agents, the composition root of an ai call.

    An agent binds the building blocks of a task together: which tools are
    active, which guards wrap each call, which sandboxes contain execution,
    and how many model calls the loop may take. The model itself is not part
    of the agent; it is bound late through the selected profile, so the same
    agent can run against the mock, a local model, or a hosted one. The class
    is resolved from the ```ai.agents``` item ```type``` (a built-in alias or
    a dotted path) by the ```app.ai``` handler, which sets it up with its config
    name and its raw declaration -- setup reads the composition out of it
    once, and ```_config``` serves the merged view.

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
        # (item or group names), merged with the profile's. governors: the
        # governor composition (guard/transformer/conductor names) for the loop
        # pipeline, in order. sandboxes: the
        # ordered sandbox chain (sandbox names) -- a tool runs in the first
        # sandbox whose tools contain it; when none does the call is denied, so
        # an in_process sandbox with tools: _all placed last is the opt-in
        # catch-all. deny: tools (item or group names) forbidden outright before
        # any sandbox lookup, a hard exclusion unlike a sandbox except.
        # omit: governor config names to drop from this agent's composition (a
        # local leaving-out, e.g. one a chain brought in), next to governors so
        # it never collides with a governor or chain name.
        # max_steps: per-agent cap on tool rounds (0 = unlimited). max_loops:
        # per-agent cap on consecutive rounds without one successful call
        # (0 = unlimited). both resolve in three levels, nearest wins: a chat()
        # call argument, then this agent option, then the ai-section base
        # (ai.max_steps / ai.max_loops). None here means "this agent sets none"
        # -- the value falls through to the ai-section base, which is the home
        # of the default numbers
        config_defaults = dict(
            tools=[],
            governors=[],
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
        self._config_name = None
        self._config_options = None

    @property
    def config_name(self):
        """
        The name this agent answers to. Never ```None``` or empty.

        The declared config key once setup handed one in, the dotted class
        (```module.Class```) until then.

        ### Returns

        - **str**: The declared key, or the dotted class

        """
        if self._config_name:
            return self._config_name
        return f'{type(self).__module__}.{type(self).__name__}'

    def _setup(self, app, config_name=None, config=None):
        """
        Set up the agent with its config.

        Called by the handler right after the build. An override must call
        ```super()._setup(...)``` first -- it builds the view ```_config```
        reads.

        ### Args

        - **app**: The Tokeo application instance
        - **config_name** (str, optional): The key the agent is declared
            under (```assistant```)
        - **config** (dict, optional): The raw ```ai.agents``` declaration

        """
        if config_name:
            self._config_name = config_name
        # deepcopy keeps the class-level config_defaults untouched
        self._config_options = deep_merge(
            copy.deepcopy(self._meta.config_defaults or {}),
            copy.deepcopy((config or {}).get('options') or {}),
        )

    def _config(self, key, fallback=None):
        """
        Return the effective value of a composition key.

        Reads the view setup built: ```Meta.config_defaults``` with the
        declared ```options``` laid over.

        ### Args

        - **key** (str): The composition key (e.g. ```tools```, ```guards```)
        - **fallback** (any, optional): Returned when the key is absent

        ### Returns

        - **any**: The effective value for the key

        ### Raises

        - **TokeoAiError**: If config options were not set by setup

        """
        if self._config_options is None:
            raise TokeoAiError(f'{type(self).__name__}: config options were not set by setup')
        return self._config_options.get(key, fallback)


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
