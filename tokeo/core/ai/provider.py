"""
The provider base class: a dumb transport that turns an already
resolved profile and a list of messages into a normalized ChatResult.
"""


class TokeoAiProvider:
    """
    Base class for ai providers.

    A provider receives an already-resolved profile and returns a
    ```ChatResult```. Its class is resolved from the profile ```type``` (a built-in
    short name or a dotted path) and instantiated with the application by the
    ```app.ai``` handler. It must not keep mutable per-call state, so that it can
    be called concurrently without locking.

    """

    def __init__(self, app, *args, **kw):
        """
        Initialize the provider.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        self.app = app

    def _setup(self, app):
        """
        Set up the provider after instantiation.

        ### Args

        - **app**: The Tokeo application instance

        """
        pass

    def chat(self, profile, messages, tools=None, model_params=None):
        """
        Send messages to the model and return a normalized result.

        ### Args

        - **profile** (dict): The resolved profile; carries ```model``` and any
            provider-specific keys (such as ```base_url``` and ```key```)
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
