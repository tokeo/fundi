"""
Resolve a guard composition into an ordered, deduplicated participation list.

This is the composition notation made executable. A composition is what an
agent's ```guards``` list (or a chain under ```ai.guards```) declares: *where and
in what
order* guards run, carrying no config (config lives in the declaration under
```ai.guards```). This module turns that notation -- bare names, ```name:
[stages]``` stage lists, ```_any```, imported chains, and the agent's ```omit```
-- into a flat, ordered list of ```(config_name, stages)``` entries the handler
can build and run.

It holds no app state and does no class loading; it works on the raw config
(the ```ai.guards``` mapping and an agent's ```guards```/```omit```) plus a
``stages_of`` callback that yields a governor's class stages (by reflection),
so the resolver stays pure and testable. Class loading, building,
and per-stage running stay in the handler.

The grammar:

- a **string** entry (```alpha```) -- additive, all the class's stages
- a **map** entry (```alpha: [on_prompt, on_call]```) -- replacing, only those
- ```[_any]``` as the stage list -- all the class's stages, explicitly
- an empty / ```null``` / ```[]``` stage list -- an error (too easily a mistake)
- a **dotted class** as the config name (```module.Class```) -- used at the
  point of use without registration; not configurable, runs with class defaults

The config name is the **name as written**: a declared name and a dotted
class are different config names even for the same class, and two declared
names for one class are two config names (two instances). The same config name
appearing more than once is deduplicated; its stages are the union across its
appearances, with nearest-wins (chain brought in vs. agent direct) deciding a
contradicting stage list.
"""

from tokeo.core.ai.exc import TokeoAiError
from tokeo.core.ai.governor import GOVERNOR_STAGE_ANY


class GovernorConfigEntry:
    """
    One resolved governor participation: a config name and its stages.

    The product of resolving a composition. ```config_name``` is the name as
    written (a declared name or a dotted class); ```stages``` is the frozen
    set of stage names it participates at, already intersected with what the
    class can do. ```source``` records where the entry came from (an agent entry
    or a chain), used to resolve a contradiction (agent wins over chain).

    ### Args

    - **config_name** (str): The name as written (a declared name or a dotted
        class); a dotted class is what the config named it there, so it is its
        config name too
    - **stages** (frozenset): The stages this entry runs at
    - **source** (str): ```'agent'``` or ```'chain'```, for nearest-wins

    """

    def __init__(self, config_name, stages, source):
        self.config_name = config_name
        self.stages = frozenset(stages)
        self.source = source

    def __repr__(self):
        return f'GovernorConfigEntry({self.config_name!r}, {sorted(self.stages)}, {self.source!r})'


def parse_entry(entry):
    """
    Parse one composition list entry into ```(config_name, stages_or_None)```.

    A string is a bare entry (additive, all stages -> stage list None). A
    single-key mapping ```{name: [stages]}``` is a replacing entry; its value
    must be a non-empty list of stage names (or ```[_any]```). Anything else is
    a malformed entry and raises (an empty / null / ```[]``` value is an error).

    ### Args

    - **entry** (str | dict): One element of a ```guards``` composition list

    ### Returns

    - **tuple**: ```(config_name, stages)```; ```stages``` is ```None``` for a bare
        entry (all the class's stages), else a list of stage names as written

    ### Raises

    - **TokeoAiError**: If the entry is malformed or carries an empty stage list

    """
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        if len(entry) != 1:
            raise TokeoAiError(f'a guard composition entry must name one guard, got {entry!r}')
        config_name, stages = next(iter(entry.items()))
        # an empty / null / [] stage list is an error -- an empty set is too
        # easily a mistake to read as intent
        if stages is None or stages == []:
            raise TokeoAiError(f'guard {config_name!r} has an empty stage list; name its stages or use the bare form')
        if not isinstance(stages, list) or not all(isinstance(stage, str) for stage in stages):
            raise TokeoAiError(f'guard {config_name!r} stage list must be a list of stage names, got {stages!r}')
        return config_name, stages
    raise TokeoAiError(f'a guard composition entry must be a name or a one-key mapping, got {entry!r}')


def _resolve_stages(config_name, stages, stages_of):
    # turn a parsed stage list (or None for bare) into the concrete stage set,
    # intersected with what the class can do. None or [_any] means all class
    # stages; an explicit list is taken as named (a named stage the class does
    # not implement is dropped here and flagged by the linter, not raised, so a
    # run is not blocked by a stale stage name)
    class_stages = frozenset(stages_of(config_name))
    if stages is None or list(stages) == [GOVERNOR_STAGE_ANY]:
        return class_stages
    named = set()
    for stage in stages:
        if stage == GOVERNOR_STAGE_ANY:
            named |= class_stages
        else:
            named.add(stage)
    return frozenset(named) & class_stages


def resolve_governors(governors_section, agent_governors, agent_omit, stages_of, logger=None):
    """
    Resolve an agent's guard composition into an ordered participation list.

    Expands chains (a list entry under ```ai.guards```), parses each entry,
    unions stages per config_name, resolves conflicts (nearest wins:
    agent over chain), applies ```omit```, and returns the entries in first-
    appearance order. The result is what the handler builds and runs.

    Conflicts:

    - same config_name, same participation (any source) -- collapses to one,
        silently.
    - same config_name at different stages from the *same* level (two chains, or
        two agent entries) -- not decidable between equals, raises.
    - agent vs chain, contradicting -- the agent wins (the nearer form); when a
        ```logger``` is given, a note is logged that the agent overrode the chain.

    ### Args

    - **governors_section** (dict): The raw ```ai.guards``` mapping (declarations,
        short forms, and chains share this namespace)
    - **agent_governors** (list): The agent's ```guards``` composition list
    - **agent_omit** (list): The agent's ```omit``` list (config names to drop)
    - **stages_of** (callable): ```config_name -> iterable of stage names``` the
        class can do (the handler passes a reflection-backed lookup)
    - **logger** (optional): A logger; when given, agent-over-chain overrides
        are reported via ```logger.warning```. ```None``` (the default) stays
        silent, so a run (ask/chat) shows no notes while ```ai lint``` can

    ### Returns

    - **list**: ```GovernorConfigEntry``` in first-appearance order, one per
        config_name, omitted config names removed

    ### Raises

    - **TokeoAiError**: On a malformed entry, an empty stage list, a chain cycle,
        or a not-decidable same-level stage conflict

    """
    # collect every appearance of each config_name (source + resolved stages), in
    # first-appearance order; the conflict rules need every appearance before
    # they can decide, so this gathers first and resolves after the walk
    order = []
    appearances = {}

    def record(config_name, stages, source):
        resolved = _resolve_stages(config_name, stages, stages_of)
        if config_name not in appearances:
            appearances[config_name] = []
            order.append(config_name)
        # keep whether the entry was bare (stages is None -> all class stages),
        # so a note can say "bare entry (all stages)" vs an explicit list
        appearances[config_name].append((source, resolved, stages is None))

    def walk(entries, source, chain_path):
        for entry in entries or []:
            config_name, stages = parse_entry(entry)
            value = governors_section.get(config_name) if isinstance(config_name, str) else None
            # a list value under ai.guards is a chain: import it (a chain
            # entry carries no stage list of its own -- it is a name)
            if isinstance(value, list):
                if stages is not None:
                    raise TokeoAiError(f'chain {config_name!r} cannot take a stage list; stages belong to its guards')
                if config_name in chain_path:
                    raise TokeoAiError(f'guard chain {config_name!r} imports itself (cycle)')
                walk(value, 'chain', chain_path + [config_name])
            else:
                record(config_name, stages, source)

    walk(agent_governors, 'agent', [])

    omit = set(agent_omit or [])
    resolved = []
    for config_name in order:
        if config_name in omit:
            continue
        resolved.append(_decide(config_name, appearances[config_name], logger))
    return resolved


def _decide(config_name, appears, logger):
    # resolve the union/conflict for one config_name from all its appearances.
    # each appearance is (source, stages, bare); an agent appearance is the
    # nearer form and wins over a chain one. within one level, differing stages
    # are not decidable (raise); same stages collapse silently
    agent_forms = [(stages, bare) for source, stages, bare in appears if source == 'agent']
    chain_forms = [(stages, bare) for source, stages, bare in appears if source == 'chain']
    # within a level, differing participation is not decidable between equals
    for forms in (agent_forms, chain_forms):
        if len({frozenset(stages) for stages, _ in forms}) > 1:
            raise TokeoAiError(f'guard {config_name!r} is registered differentially more than once, this is not decidable')
    # nearest wins: an agent form (if any) overrides a chain form completely
    if agent_forms:
        agent_stages, agent_bare = agent_forms[0]
        if chain_forms:
            chain_stages, _ = chain_forms[0]
            if logger is not None and frozenset(agent_stages) != frozenset(chain_stages):
                agent_desc = 'bare entry (all stages)' if agent_bare else _fmt(agent_stages)
                logger.warning(f"guard {config_name!r}: the agent's {agent_desc} overrides the chain's {_fmt(chain_stages)}")
        return GovernorConfigEntry(config_name, agent_stages, 'agent')
    return GovernorConfigEntry(config_name, chain_forms[0][0], 'chain')


def _fmt(stages):
    # a stage set as a stable, readable list for a note; the bare-name case
    # (all the class's stages) reads as "bare entry (all stages)"
    return f'[{", ".join(sorted(stages))}]' if stages else '[]'
