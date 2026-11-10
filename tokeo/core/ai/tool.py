"""
The tool base class: a callable capability the model may invoke. A
tool decides its own app need in __init__ (none by default) and yields
the same app class everywhere (the uniformity rule).

.. include:: ./TOOLS.md
"""

import copy
from dataclasses import fields

from cement.core.meta import MetaMixin

from tokeo.core.utils.dict import deep_merge
from tokeo.core.ai.exc import TokeoAiError
from tokeo.core.ai.data import ToolResult, ToolValue, ToolStates
from tokeo.core.utils.json import dump_json, TokeoJsonUnknownNoneEncoder


class TokeoAiTool(MetaMixin):
    """
    Base class for agent tools.

    A tool's class is resolved from its ```ai.tools``` item ```type``` (a built-in
    alias or a class path) and instantiated with the application by the
    ```app.ai``` handler, which then sets it up with its config name and its raw
    item; setup reads the ```options``` out of it once, and
    ```_config``` serves the merged view. It can use ```app.db```, the vault,
    and hold resources.
    ```Meta``` declares the ```description``` and the JSON-schema ```parameters```
    sent to the model, plus any option of the tool's own (overridden per
    item by its ```options```); a subclass overrides those keys and ```exec```
    does the work. The handler reads them from ```_meta```.

    Around ```exec``` a tool may implement four optional hooks, run in this
    order: ```prepare```, ```before```, ```exec```, ```after```, ```teardown```.
    All four run in-process, outside the sandbox, and receive the run's
    ```turndata```, the call ```arguments``` and a ```track``` to note something
    on the trace. A tool get's no context and no loopdata etc. ```prepare``` and
    ```teardown``` are the outer frame (open and close a resource, note something
    in turndata) and return nothing. ```before``` and ```after``` are the inner
    pair that reshapes what goes in and what comes out.

    The hooks reshape data: because they run outside the sandbox they may read
    and reshape arguments and results, but must not carry out what the model
    asked for; that stays in ```exec```, behind the wall.

    A raise in ```before```, ```exec``` or ```after``` becomes the call's result
    (carrying the exception), skipping the rest and going straight to
    ```teardown```. ```teardown``` runs whenever ```prepare``` succeeded; if
    ```prepare``` itself raises, nothing was set up and ```teardown``` is
    skipped.

    ### Args

    - **app**: The Tokeo application instance
    - **args**: Positional arguments for the parent initializer

    ### Keyword Args

    - **kw**: Keyword arguments for the parent initializer

    """

    class Meta:
        """Tool meta-data."""

        # short description the model sees
        description = ''

        # json-schema object describing the arguments the model may pass
        parameters = {}

        # the configurable defaults, as one dict; empty here: a tool's
        # description and parameters above are the model interface, not config
        # options. a derivation that has its own configurable options fills
        # this (the config_defaults rule: every Meta carries the dict)
        config_defaults = {}

    def __init__(self, app, *args, **kw):
        super().__init__(*args, **kw)
        #: The Tokeo application instance
        self.app = app
        self._config_name = None
        self._config_options = None

    @property
    def config_name(self):
        """
        The name this tool answers to. Never ```None``` or empty.

        The configured key once setup handed one in, the class path
        (```module:Class```) until then.

        ### Returns

        - **str**: The configured key, or the class path

        """
        if self._config_name:
            return self._config_name
        return f'{type(self).__module__}:{type(self).__name__}'

    def _setup(self, app, config_name=None, item=None):
        """
        Set up the tool with its config.

        Called by the handler right after the build. An override must call
        ```super()._setup(...)``` first; it builds the view ```_config```
        reads.

        ### Args

        - **app**: The Tokeo application instance
        - **config_name** (str, optional): The key the tool is configured
            under (```calc```)
        - **item** (dict, optional): The raw ```ai.tools``` item

        """
        if config_name:
            self._config_name = config_name
        # deepcopy keeps the class-level config_defaults untouched
        self._config_options = deep_merge(
            copy.deepcopy(self._meta.config_defaults or {}),
            copy.deepcopy((item or {}).get('options') or {}),
        )

    def _config(self, key, *, fallback=None):
        """
        Return the effective value of an option key.

        Reads the view setup built: ```Meta.config_defaults``` with the
        configured ```options``` laid over.

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

    def prepare(self, turndata, arguments, track):
        """
        Set up before the call runs; the outer frame's opening half.

        Open a resource, or note something in ```turndata``` for a later hook or
        governor. Returns nothing and cannot steer the call; its only way to
        stop ```exec``` is to raise. Succeeding here is what arms ```teardown```.

        ### Args

        - **turndata** (TokeoAiTurndata): The run's shared data area
        - **arguments** (dict): The parsed call arguments, read-only here
        - **track** (func): Call it with a message or data to note something
            on the trace

        """

    def before(self, turndata, arguments, track):
        """
        Reshape the arguments just before the call; a governor at the tool.

        Returns the arguments to run with, as a dict the loop passes on and
        ```exec``` unpacks; ```None``` leaves them unchanged. What it returns
        must stay JSON-able, like all sandbox arguments. May delegate via
        ```self.app.ai.chat(..., turndata=turndata)```; passing
        ```turndata``` shares a recursion mark across that boundary.

        ### Args

        - **turndata** (TokeoAiTurndata): The run's shared data area
        - **arguments** (dict): The parsed call arguments
        - **track** (func): Call it with a message or data to note something
            on the trace

        ### Returns

        - **dict|None**: The arguments to run; ```None``` keeps them as-is

        """

    def exec(self, **arguments):
        """
        Execute the tool and return its result.

        ### Keyword Args

        - **arguments**: The parsed arguments for the call

        ### Returns

        - **ToolResult|object**: The result; a plain value is wrapped by
            ```create_tool_result```, which builds its three views

        """
        raise NotImplementedError

    def after(self, turndata, arguments, result, track):
        """
        Reshape the result after a successful call; a governor at the tool.

        Runs only on the success path; it reshapes a result that would not
        exist otherwise. Returns the result to use; ```None``` keeps it. Enrich
        the result here, do not re-run the call outside the sandbox.

        ### Args

        - **turndata** (TokeoAiTurndata): The run's shared data area
        - **arguments** (dict): The arguments as reshaped by ```before```
        - **result** (ToolResult): The result ```exec``` produced
        - **track** (func): Call it with a message or data to note something
            on the trace

        ### Returns

        - **ToolResult|None**: The result to use; ```None``` keeps it as-is

        """

    def teardown(self, turndata, arguments, result, track):
        """
        Tear down after the call; the outer frame's closing half.

        Close what ```prepare``` opened and record the outcome; being the only
        hook that always runs, it is the one place that sees every ending.
        ```result``` is always a ```ToolResult```; on a failed call it carries
        the exception in ```result.state.error```. Raising here is recorded
        without overturning the result the call already produced.

        ### Args

        - **turndata** (TokeoAiTurndata): The run's shared data area
        - **arguments** (dict): The arguments as reshaped by ```before```
        - **result** (ToolResult): The outcome; carries the exception on failure
        - **track** (func): Call it with a message or data to note something
            on the trace

        """


class TokeoAiToolResultJsonEncoder(TokeoJsonUnknownNoneEncoder):
    """
    Encoder that names an unknown object and records that it did so.

    Used once by ```create_tool_result``` to build ```as_json``` and derive
    ```is_incomplete``` in a single encode pass: json calls ```encode``` for every
    value it cannot serialize, so substituting there (the type name in place of
    the object) and flipping a flag tells whether the json form is faithful.

    """

    def __init__(self):
        super().__init__()
        #: (bool) Flipped the moment the base returns None for an unknown
        #: object, so after one dump_json it tells whether anything was
        #: substituted
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
    - **as_str** (str|None): The model-facing string; defaults to
        ```str(value)```, or the empty string for a ```None``` value, when not
        given
    - **state** (dict|None): Run states to carry as a dict of field names
        (```stdout```, ```stderr```, ```error```, ```is_incomplete```); only the
        named fields are set onto the derived states, so a partial dict keeps the
        derived ```is_incomplete```. With ```None``` the states are derived from the
        encoding alone

    ### Returns

    - **ToolResult**: With a ```ToolValue``` built from the value and the states

    """
    # one encode pass: the encoder names every object it has to substitute and
    # flips its flag, so is_incomplete is observed in the same dump as as_json
    encoder = TokeoAiToolResultJsonEncoder()
    as_json = dump_json(value, encoder=encoder)
    # always a fresh states owned by this result (so nothing the caller holds is
    # aliased); is_incomplete starts from the encoder's finding, then a state
    # dict sets only its named fields onto it; a partial dict keeps the derived
    # is_incomplete. walking fields() covers a new ToolStates field untouched here
    toolstate = ToolStates(is_incomplete=encoder.substituted)
    if isinstance(state, dict):
        for f in fields(ToolStates):
            if f.name in state:
                setattr(toolstate, f.name, state[f.name])
    # default the model-facing string from the value, but a None value has no
    # text, so show empty rather than the literal 'None'; an explicit as_str wins
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
