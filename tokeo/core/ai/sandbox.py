"""
The sandbox base class: the wall that contains a tool's execution. A
guard decides whether a call may run; the sandbox is where it runs.
"""

import copy

from cement.core.meta import MetaMixin

from tokeo.core.utils.dict import deep_merge


class TokeoAiSandbox(MetaMixin):
    """
    Base class for sandboxes that contain a tool's execution.

    A guard decides whether a call may run; a sandbox is the dumb wall that
    contains the run. The handler loop calls ```sandbox.exec(tool, arguments)```
    in place of a bare ```tool.exec```; the ```on_call```/```on_return``` guards
    stay around that seam. The sandbox is chosen per agent: a tool runs in the
    first sandbox of
    the agent's chain whose tools contain it (the ```ai.sandboxes```
    selection), so the same tool can run in process, in a subprocess, or
    in a container without knowing where. Both layers use the verb
    ```exec``` (never ```run```).

    Its class is resolved from the ```ai.sandboxes``` item ```type``` (a built-in
    short name or a dotted path) and instantiated with the application and the
    item's ```options``` as Meta overrides by the ```app.ai``` handler. Like a
    provider, it holds no mutable per-call state.

    ### Notes

    : Honesty over promises. A sandbox only enforces what its mechanism truly
        can: ```in_process``` isolates nothing, ```subprocess``` is fault/resource
        isolation (a memory cap and a wall-clock timeout), not a jail. Real
        path or network isolation needs a container/VM/WASM backend the user
        supplies.

    """

    class Meta:
        """Sandbox meta-data; a sandbox defines its own option keys.

        The base declares an empty ```config_defaults```: which options exist (a
        timeout, a memory cap, a container name, a scratch mount ...) is the
        concrete mechanism's business, declared in its own ```config_defaults```
        and checked by its ```validate_options```. ```in_process``` has none.
        """

        # the configurable defaults, as one dict; the item's options overlay
        # this, read at runtime via _config. empty here: the base has no options
        config_defaults = {}

    def __init__(self, app, *args, **kw):
        """
        Initialize the sandbox.

        ### Args

        - **app**: The Tokeo application instance
        - **args**: Positional arguments for the parent initializer

        ### Keyword Args

        - **kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiSandbox, self).__init__(*args, **kw)
        self.app = app
        # the raw ai.sandboxes[name] declaration, set by the handler after build
        # (the sandbox reads its own options out of it); None when built without
        # a declaration (e.g. directly in a test)
        self._declaration = None
        # the effective options (config_defaults overlaid with the declaration's
        # options), built lazily by _config and cached
        self._options = None

    def _setup(self, app):
        """
        Set up the sandbox after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def _config(self, key, fallback=None):
        """
        Return the effective value of an option key.

        Built once, lazily, and cached: ```Meta.config_defaults``` deep-merged
        with the declaration's ```options``` (the Tokeo config rule -- lists
        append, dicts merge, scalars replace).

        ### Args

        - **key** (str): The option key (e.g. ```timeout```, ```memory_mb```)
        - **fallback** (any, optional): Returned when the key is absent

        ### Returns

        - **any**: The effective value for the key

        """
        if self._options is None:
            defaults = self._meta.config_defaults or {}
            options = (self._declaration or {}).get('options') or {}
            # deepcopy so the class-level config_defaults is never mutated
            self._options = deep_merge(copy.deepcopy(defaults), copy.deepcopy(options))
        return self._options.get(key, fallback)

    def exec(self, tool, arguments):
        """
        Execute a tool call inside this sandbox and return its result.

        The single method a derivation implements; it chooses the mechanism
        (call in process, spawn a worker subprocess, ```docker exec``` ...). The
        outer contract is the tool call: JSON-able ```arguments``` in, a
        ```ToolResult``` out. Across a process boundary only the JSON-able
        arguments and the ```ToolResult``` text/data cross; in process any
        object is fine.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool to run
        - **arguments** (dict): The parsed call arguments

        ### Returns

        - **ToolResult | str**: The tool's result; a plain string is treated
            as the model-facing text

        """
        raise NotImplementedError

    def validate_options(self, options):
        """
        Validate the item's ```options``` for the linter.

        The linter does not know a sandbox's allowed keys; it asks the class.
        The base accepts anything (a permissive default); a derivation that
        wants strict checking overrides this and returns error strings.

        ### Args

        - **options** (dict): The item's ```options``` block as configured

        ### Returns

        - **list[str] | None**: Error messages, or ```None```/empty when valid

        """
        return None
