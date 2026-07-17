"""
The tool base class: a callable capability the model may invoke. A
tool decides its own app need in __init__ (none by default) and yields
the same app class everywhere (the uniformity rule).
"""

import copy
from dataclasses import fields

from cement.core.meta import MetaMixin

from tokeo.core.utils.dict import deep_merge
from tokeo.core.ai.exc import TokeoAiError
from tokeo.core.ai.data import ToolResult, ToolValue, ToolStates
from tokeo.core.utils.json import json_dump, TokeoJsonUnknownNoneEncoder


class TokeoAiTool(MetaMixin):
    """
    Base class for agent tools.

    A tool's class is resolved from its ```ai.tools``` item ```type``` (a built-in
    alias or a dotted path) and instantiated with the application by the
    ```app.ai``` handler, which then sets it up with its config name and its raw
    declaration -- setup reads the ```options``` out of it once, and
    ```_config``` serves the merged view. It can use ```app.db```, the vault,
    and hold resources.
    ```Meta``` declares the ```description``` and the JSON-schema ```parameters```
    sent to the model, plus any setting of the tool's own (overridden per
    item by its ```options```); a subclass overrides those keys and ```exec```
    does the work. The handler reads them from ```_meta```.

    """

    class Meta:
        """Tool meta-data sent to the model."""

        # short description the model sees
        description = ''

        # json-schema object describing the arguments the model may pass
        parameters = {}

        # the configurable defaults, as one dict; empty here -- a tool's
        # description and parameters above are the model interface, not config
        # settings. a derivation that has its own configurable settings fills
        # this (the config_defaults rule: every Meta carries the dict)
        config_defaults = {}

    def __init__(self, app, *args, **kw):
        """
        Initialize the tool.

        ### Args

        - **app**: The Tokeo application instance
        - **args**: Positional arguments for the parent initializer

        ### Keyword Args

        - **kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiTool, self).__init__(*args, **kw)
        self.app = app
        self._config_name = None
        self._config_options = None

    @property
    def config_name(self):
        """
        The name this tool answers to. Never ```None``` or empty.

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
        Set up the tool with its config.

        Called by the handler right after the build. An override must call
        ```super()._setup(...)``` first -- it builds the view ```_config```
        reads.

        ### Args

        - **app**: The Tokeo application instance
        - **config_name** (str, optional): The key the tool is declared
            under (```calc```)
        - **config** (dict, optional): The raw ```ai.tools``` declaration

        """
        if config_name:
            self._config_name = config_name
        # deepcopy keeps the class-level config_defaults untouched
        self._config_options = deep_merge(
            copy.deepcopy(self._meta.config_defaults or {}),
            copy.deepcopy((config or {}).get('options') or {}),
        )

    def _config(self, key, *, fallback=None):
        """
        Return the effective value of an option key.

        Reads the view setup built: ```Meta.config_defaults``` with the
        declared ```options``` laid over.

        ### Args

        - **key** (str): The option key
        - **fallback** (any, optional): Returned when the key is absent

        ### Returns

        - **any**: The effective value for the key

        ### Raises

        - **TokeoAiError**: If config options were not set by setup

        """
        if self._config_options is None:
            raise TokeoAiError(f'{type(self).__name__}: config options were not set by setup')
        return self._config_options.get(key, fallback)

    def exec(self, **arguments):
        """
        Execute the tool and return its result.

        ### Args

        - ****arguments**: The parsed arguments for the call

        ### Returns

        - **ToolResult | str**: The result; a plain string is treated as the
            model-facing text

        """
        raise NotImplementedError


class TokeoJsonAiToolResultEncoder(TokeoJsonUnknownNoneEncoder):
    """
    Encoder that names an unknown object and records that it did so.

    Used once by ```create_tool_result``` to build ```as_json``` and derive
    ```incomplete``` in a single encode pass: json calls ```encode``` for every
    value it cannot serialize, so substituting there (the type name in place of
    the object) and flipping a flag tells whether the json form is faithful.

    """

    def __init__(self):
        super().__init__()
        # flipped the moment the base returns None for an unknown object, so
        # after one json_dump it tells whether anything was substituted
        self.substituted = False

    def encode(self, obj):
        """
        Encode like the base, but name an unknown object and mark the run.

        ### Args

        - **obj** (any): The object json could not serialize itself

        ### Returns

        - **str|dict**: As the base for a handled type; the object's type name
            for any other object, with ```substituted``` set so the caller
            learns the json form is not faithful

        """
        result = super().encode(obj)
        if result is None:
            # the base handled no known type, so json would render null; name
            # the object instead and mark that the json form is not faithful
            self.substituted = True
            return type(obj).__name__
        return result


def create_tool_result(value, as_str=None, state=None):
    """
    Build a ```ToolResult``` from a value, the path a tool uses for fine control.

    The trivial path is to return a plain value and let the framework wrap it;
    this helper is for a tool that wants to set the views or the run states
    itself (e.g. a file tool reporting a structured result and a note).

    ### Args

    - **value**: The delivered value (mandatory); becomes ```as_data```, and the
        base for ```as_json``` and the ```as_str``` default
    - **as_str** (str | None): The model-facing string; defaults to
        ```str(value)```, or the empty string for a ```None``` value, when not
        given
    - **state** (dict | None): Run states to carry as a dict of field names
        (```stdout```, ```stderr```, ```exception```, ```incomplete```); only the
        named fields are set onto the derived states, so a partial dict keeps the
        derived ```incomplete```. With ```None``` the states are derived from the
        encoding alone

    ### Returns

    - **ToolResult**: with a ```ToolValue``` built from the value and the states

    """
    # one encode pass: the encoder names every object it has to substitute and
    # flips its flag, so incomplete is observed in the same dump as as_json
    encoder = TokeoJsonAiToolResultEncoder()
    as_json = json_dump(value, encoder=encoder)
    # always a fresh states owned by this result (so nothing the caller holds is
    # aliased); incomplete starts from the encoder's finding, then a state dict
    # sets only its named fields onto it -- a partial dict keeps the derived
    # incomplete. walking fields() covers a new ToolStates field untouched here
    toolstate = ToolStates(incomplete=encoder.substituted)
    if isinstance(state, dict):
        for f in fields(ToolStates):
            if f.name in state:
                setattr(toolstate, f.name, state[f.name])
    # default the model-facing string from the value, but a None value has no
    # text -- show empty rather than the literal 'None'; an explicit as_str wins
    if as_str is None:
        as_str = '' if value is None else str(value)
    return ToolResult(
        value=ToolValue(
            as_str=as_str,
            as_json=as_json,
            as_data=value,
        ),
        state=toolstate,
    )
