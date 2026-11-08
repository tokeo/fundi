"""
Resolve which profile a run uses, and the agent the profile binds.

A profile is a named entry under ```ai.profiles```; it binds a provider (the
model, by ```type```) and an agent (the composition that runs tools), and may
carry a ```model```/```purpose``` selector. A run picks one profile (by name,
by model, by purpose, or the configured default) and from it the agent to run.

It holds no app state and does no class loading; it works on the raw
```profiles``` mapping plus the selector and the default, so the handler and the
linter draw from the same source for "which profile" and "which agent".
"""

from tokeo.core.ai.exc import TokeoAiError


def _enabled(profile):
    # a profile counts only when it is a mapping and not disabled; a disabled
    # profile is skipped everywhere, so it is also not found by its name
    return isinstance(profile, dict) and bool(profile.get('enabled', True))


def get_profile(profiles, key, value):
    """
    Resolve a single enabled profile by name or by a field value.

    ### Args

    - **profiles** (dict): The ```ai.profiles``` mapping
    - **key** (str): ```profile``` or ```name``` matches the profile name; any
        other key (```model```, ```purpose```) matches that field at the
        profile top level
    - **value**: The value the key must equal

    ### Returns

    - **tuple**: ```(name, profile)``` of the matching profile

    ### Raises

    - **TokeoAiError**: If no enabled profile matches (on a field match the first
        enabled profile in config order wins)

    """
    if key in ('profile', 'name'):
        profile = profiles.get(value)
        if _enabled(profile):
            return value, profile
        raise TokeoAiError(f'no enabled ai profile named {value!r}')
    for name, profile in profiles.items():
        if not _enabled(profile):
            continue
        if profile.get(key) == value:
            return name, profile
    raise TokeoAiError(f'no enabled ai profile with {key}={value!r}')


def resolve_profile(profiles, default_profile, profile=None, model=None, purpose=None):
    """
    Resolve the profile to run, by a single selector or the default.

    At most one of ```profile```/```model```/```purpose``` may be given; with
    none, the configured default profile is used; with no default at all this
    raises (there is no code fallback).

    ### Args

    - **profiles** (dict): The ```ai.profiles``` mapping
    - **default_profile** (str|None): ```ai.defaults.profile```
    - **profile** (str, optional): Selects the profile by its name
    - **model** (str, optional): Selects the profile by its model
    - **purpose** (str, optional): Selects the profile by its purpose

    ### Returns

    - **tuple**: ```(name, profile)``` of the matching profile

    ### Raises

    - **TokeoAiError**: If more than one selector is given, or none matches, or
        no selector and no default is configured

    """
    keys = {'profile': profile, 'model': model, 'purpose': purpose}
    active = {k: v for k, v in keys.items() if v is not None}
    if len(active) > 1:
        raise TokeoAiError('select a profile by only one of profile, model or purpose')
    if active:
        key, value = next(iter(active.items()))
        return get_profile(profiles, key, value)
    if not default_profile:
        raise TokeoAiError('no ai profile selected and no ai.defaults.profile configured')
    return get_profile(profiles, 'profile', default_profile)


def resolve_agent_name(agent, profile, default_agent):
    """
    Return the agent name a run uses, in order: call argument, profile, defaults.

    An explicit call ```agent``` wins. Else the selected profile's ```agent```:
    a name selects that agent, ```agent: null``` means no agent on purpose
    (overriding the default). A profile without the key falls through to
    ```ai.defaults.agent```. ```None``` means no agent results; the handler then
    runs the implicit agent, which offers no tool.

    ### Args

    - **agent** (str|None): The explicit call argument, if any
    - **profile** (dict|None): The selected profile (its ```agent``` key, if
        present, is honoured even when ```null```)
    - **default_agent** (str|None): ```ai.defaults.agent```

    ### Returns

    - **str|None**: The agent name to build, or ```None``` when none is named

    """
    if agent is None and profile is not None and 'agent' in profile:
        agent = profile.get('agent')
        if not agent:
            return None
    if agent is None:
        agent = default_agent
    if not agent:
        return None
    return agent


def get_selectable_names(profiles, agent_names):
    """
    The names selectable on the command line, grouped by selector.

    ### Args

    - **profiles** (dict): The ```ai.profiles``` mapping
    - **agent_names** (iterable): The configured agent names

    ### Returns

    - **dict**: ```{'profile': [...], 'agent': [...], 'model': [...],
        'purpose': [...]}```, only enabled profiles contributing

    """
    enabled_profiles = [(name, profile) for name, profile in profiles.items() if _enabled(profile)]
    return {
        'profile': [name for name, _ in enabled_profiles],
        'agent': list(agent_names),
        'model': sorted({p.get('model') for _, p in enabled_profiles} - {None}),
        'purpose': sorted({p.get('purpose') for _, p in enabled_profiles} - {None}),
    }
