"""
The provider base class: a dumb transport that turns an already
resolved profile and a list of messages into a normalized ChatResult.
"""


class TokeoAiProvider:
    """
    Base class for ai providers.

    A provider receives an already-resolved profile and returns a
    ```ChatResult```. Its class is resolved from the profile ```type``` (a built-in
    alias or a class path) and instantiated with the application by the
    ```app.ai``` handler. It must not keep mutable per-call state, so that it can
    be called concurrently without locking.

    ### Args

    - **app**: The Tokeo application instance
    - **args**: Positional arguments for the parent initializer

    ### Keyword Args

    - **kw**: Keyword arguments for the parent initializer

    """

    class Meta:
        """Provider meta-data; a provider defines its own option keys."""

        # the keys a profile may set under options, with their defaults
        config_defaults = {}

    def __init__(self, app, *args, **kw):
        #: The Tokeo application instance
        self.app = app
        # never configured under a config key; the property reports the class
        self._config_name = None

    @property
    def config_name(self):
        """
        The name this provider answers to. Never ```None``` or empty.

        A provider is never configured under a config key (one object serves
        every profile of its type), so this reports the class path.

        ### Returns

        - **str**: The class path

        """
        if self._config_name:
            return self._config_name
        return f'{type(self).__module__}:{type(self).__name__}'

    def _setup(self, app, config_name=None, config=None):
        """
        Set up the provider.

        Same form as every other ai class; a provider has nothing to receive;
        its options come from the profile it is handed per chat call.

        ### Args

        - **app**: The Tokeo application instance
        - **config_name** (str, optional): Never handed in for a provider
        - **config** (dict, optional): Never handed in for a provider

        """
        if config_name:
            self._config_name = config_name

    def chat(self, profile, messages, tools=None, model_params=None):
        """
        Send messages to the model and return a normalized result.

        ### Args

        - **profile** (dict): The resolved profile; carries ```model``` and,
            under ```options```, the provider's own keys (its
            ```Meta.config_defaults```, such as ```base_url``` and ```key```)
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list|None): Optional tool definitions for the call
        - **model_params** (dict|None): Per-call model parameters that override
            the profile's ```model_params``` (temperature, top_p, ...); a hook
            may pass adjusted values without touching the config. Providers that
            do not drive a configurable model (mock, akili) ignore it

        ### Returns

        - **ChatResult**: The normalized response

        """
        raise NotImplementedError
