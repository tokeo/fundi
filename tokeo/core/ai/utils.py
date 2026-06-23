"""
Small, dependency-light helpers shared across the ai layer.

Kept here (not in the ext) so any consumer -- the cli controller, a provider,
a project's own code -- can use them without importing the cement extension.
"""

import yaml

from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.data import TraceStep
from tokeo.core.utils.json import TokeoJsonUnknownNameEncoder


def coerce_model_param_value(raw):
    """
    Coerce a raw ```key=value``` value the way the yaml config handler coerces
    an environment override.

    Runs the string through ```yaml.safe_load```, so ```0.2```/```42```/
    ```true```/```null``` get their proper types and anything else stays a
    string. This mirrors the env-override coercion on purpose -- same rule for
    a value typed on the command line as for one injected via the environment.
    It is a deliberate four-line clone rather than a cross-module import, and
    the env-only ```!```-tag rejection is not wanted here.

    ### Args

    - **raw** (str): The raw value text (already stripped)

    ### Returns

    - The coerced scalar, or the original string when it is not a yaml scalar

    """
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def parse_model_params(pairs):
    """
    Turn a list of ```key=value``` strings into a model_params dict.

    The value is coerced like a yaml scalar (see ```coerce_model_param_value```);
    a null or empty value removes the key, so a call can drop a parameter and
    fall back to the profile's value. Shared by ```ai ask```, the ```ai chat```
    start flags and the interactive chat switches, so one rule holds everywhere.

    ### Args

    - **pairs** (list|None): The raw ```key=value``` strings to parse

    ### Returns

    - **dict**: The parsed and coerced model parameters

    ### Raises

    - **TokeoAiError**: On a token without ```=``` or with an empty key

    """
    params = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition('=')
        key = key.strip()
        if not sep or not key:
            raise TokeoAiError(f'model_param expects key=value, got {pair!r}')
        value = coerce_model_param_value(raw.strip())
        if value is None:
            # null or empty removes the key, shell-independent (no quoting trap)
            params.pop(key, None)
        else:
            params[key] = value
    return params


class TokeoJsonAiTraceEncoder(TokeoJsonUnknownNameEncoder):
    """
    JSON encoder for the ```ai ask --trace``` export.

    Extends the name encoder; in compact mode it drops the ```object``` field
    of an unchanged ```TraceStep```, since that object only repeats what the
    last changed step already showed.

    """

    def __init__(self, compact=False):
        super().__init__()
        self.compact = compact

    def encode(self, obj):
        """
        Encode a trace step, dropping its object on an unchanged compact step.

        A ```TraceStep``` with ```changed=False``` left its object exactly as the
        last changed step already showed it, so its object is pure repetition;
        in compact mode this renders the step without the ```object``` field. A
        changed step keeps its object. Everything else delegates to the base
        name encoder, so a date, dataclass, or live origin renders the same as
        in the full export (an unknown object as its type name).

        ### Args

        - **obj** (any): The object json could not serialize on its own

        ### Returns

        - **dict|str**: A step dict (without ```object``` when unchanged), or the
            base encoder's result for anything else

        """
        # a trace step: render its fields, and drop the object when the step is
        # unchanged -- an unchanged step repeats the object the last changed step
        # already showed, so it is pure repetition in the compact view. a changed
        # step keeps its object (it shows what actually changed). the changed flag
        # is read off the built dict, after the type is known
        if self.compact and isinstance(obj, TraceStep):
            step = dict(obj.__dict__)
            if not step['changed']:
                del step['object']
            return step
        return super().encode(obj)
