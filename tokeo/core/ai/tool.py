"""
The tool base class: a callable capability the model may invoke. A
tool decides its own app need in __init__ (none by default) and yields
the same app class everywhere (the uniformity rule).
"""

from cement.core.meta import MetaMixin


class TokeoAiTool(MetaMixin):
    """
    Base class for agent tools.

    A tool's class is resolved from its ```ai.tools``` item ```type``` (a built-in
    short name or a dotted path) and instantiated with the application and the
    item's ```options``` as Meta overrides by the ```app.ai``` handler, so it can
    read configuration, use ```app.db```, the vault, and hold resources.
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
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiTool, self).__init__(*args, **kw)
        self.app = app

    def _setup(self, app):
        """
        Set up the tool after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

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
