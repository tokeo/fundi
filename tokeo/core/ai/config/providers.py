"""
Resolve the provider a profile uses.

A provider is the model behind a profile -- built by ```type``` like any other
component (mock, an OpenAI-compatible endpoint, ...). The one piece of
"resolution" the provider side has is reading the provider ```type``` out of a
selected profile; building the class stays in the handler.

It holds no app state and does no class loading; it works on the raw profile, so
the handler and the linter draw from the same source for "which provider type".
"""

from tokeo.core.ai.exc import TokeoAiError


def provider_type_of(name, profile):
    """
    Return the provider ```type``` declared on a profile.

    ### Args

    - **name** (str): The profile name (for the error message)
    - **profile** (dict): The selected profile

    ### Returns

    - **str**: The provider type (a built-in alias or a dotted path)

    ### Raises

    - **TokeoAiError**: If the profile has no ```type```

    """
    provider_type = (profile or {}).get('type')
    if not provider_type:
        raise TokeoAiError(f'ai profile {name!r} is missing a type')
    return provider_type
