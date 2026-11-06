"""
Tests for the config resolvers (tokeo/core/ai/config).

The config package holds the pure resolvers that turn the raw ai configuration
into the shapes the handler runs, one module per component kind. These tests
pin each resolver's rules in isolation, without the handler or class loading
(stubs stand in for reflection and lookups):

- ```guards```: the composition notation (bare names, ```name: [stages]```
  stage lists, ```_any```, chains, ```omit```) and the conflict rules.
- ```tools```: group expansion and cycle finding.
- ```sandboxes```: "does this sandbox contain this tool" and the chain walk.
- ```profiles```: profile selection (by name/model/purpose/default) and the
  agent a profile binds.
"""

import pytest

from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.governor import GOVERNOR_STAGE_ANY
from tokeo.core.ai.config.governors import resolve_governors, parse_entry
from tokeo.core.ai.config.tools import resolve_tools, find_cycles
from tokeo.core.ai.config.sandboxes import sandbox_contains_tool, sandbox_for
from tokeo.core.ai.config.profiles import resolve_profile, find_profile, resolve_agent_name


# a stub class-stage lookup: every config name can do these three stages, so the
# tests target the composition logic, not reflection
def _get_class_stages(config_name):
    return ['on_prompt', 'on_call', 'on_return']


def _resolve_governors(guards_section, agent_guards, agent_omit=None):
    return resolve_governors(guards_section, agent_guards, agent_omit or [], _get_class_stages)


def test_bare_name_runs_all_class_stages():
    """A bare 'alpha' is additive: it runs at all three stages its class can do."""
    entries = _resolve_governors({}, ['alpha'])
    assert len(entries) == 1
    assert entries[0].config_name == 'alpha'
    assert entries[0].stages == frozenset({'on_prompt', 'on_call', 'on_return'})


def test_stage_list_runs_only_named_stages():
    """{'alpha': ['on_call']} replaces the class stages: only on_call runs."""
    # the listed stages are intersected with what the class can do
    entries = _resolve_governors({}, [{'alpha': ['on_call']}])
    assert entries[0].stages == frozenset({'on_call'})


def test_any_token_means_all_class_stages():
    """[_any] is the explicit form of the bare name: all three class stages run."""
    entries = _resolve_governors({}, [{'alpha': [GOVERNOR_STAGE_ANY]}])
    assert entries[0].stages == frozenset({'on_prompt', 'on_call', 'on_return'})


def test_named_stage_the_class_cannot_do_is_dropped():
    """on_begin, which the class cannot do, is dropped and only on_call is left."""
    # the linter flags such a stage; the resolver does not fail the run
    entries = _resolve_governors({}, [{'alpha': ['on_call', 'on_begin']}])
    assert entries[0].stages == frozenset({'on_call'})


def test_empty_stage_list_is_an_error():
    """A [] or a null stage list raises TokeoAiError, too easily a mistake."""
    with pytest.raises(TokeoAiError):
        _resolve_governors({}, [{'alpha': []}])
    with pytest.raises(TokeoAiError):
        _resolve_governors({}, [{'alpha': None}])


def test_order_is_first_appearance():
    """alpha, beta, gamma come back in the order they first appear."""
    entries = _resolve_governors({}, ['alpha', 'beta', 'gamma'])
    assert [e.config_name for e in entries] == ['alpha', 'beta', 'gamma']


def test_same_config_name_same_stages_collapses_silently():
    """{'alpha': ['on_call']} named twice collapses silently into one entry."""
    entries = _resolve_governors({}, [{'alpha': ['on_call']}, {'alpha': ['on_call']}])
    assert len(entries) == 1
    assert entries[0].stages == frozenset({'on_call'})


def test_same_config_name_different_stages_same_level_is_error():
    """alpha at on_call and at on_return on one level is not decidable: raises."""
    # to run it at several stages, name them in one stage list
    with pytest.raises(TokeoAiError):
        _resolve_governors({}, [{'alpha': ['on_call']}, {'alpha': ['on_return']}])


def test_a_chain_is_expanded_in_place():
    """A chain under ai.guards expands in place: alpha, beta (on_call), gamma."""
    section = {'my_chain': ['alpha', {'beta': ['on_call']}]}
    entries = _resolve_governors(section, ['my_chain', 'gamma'])
    assert [e.config_name for e in entries] == ['alpha', 'beta', 'gamma']
    assert entries[1].stages == frozenset({'on_call'})


def test_a_chain_may_import_another_chain():
    """The chain outer imports the chain inner: alpha, then beta."""
    section = {'inner': ['alpha'], 'outer': ['inner', 'beta']}
    entries = _resolve_governors(section, ['outer'])
    assert [e.config_name for e in entries] == ['alpha', 'beta']


def test_a_chain_cycle_is_an_error():
    """Chains a and b importing each other raise TokeoAiError (cycle)."""
    section = {'a': ['b'], 'b': ['a']}
    with pytest.raises(TokeoAiError):
        _resolve_governors(section, ['a'])


def test_two_chains_contradicting_is_an_error():
    """chainA and chainB give alpha different stages: no nearer form, raises."""
    section = {'chainA': [{'alpha': ['on_call']}], 'chainB': [{'alpha': ['on_return']}]}
    with pytest.raises(TokeoAiError):
        _resolve_governors(section, ['chainA', 'chainB'])


def test_agent_over_chain_logs_a_note_and_agent_wins():
    """The agent's alpha at on_return beats the chain's, one note is logged."""
    # nearest wins
    section = {'chainA': [{'alpha': ['on_call']}]}
    notes = []

    class _Log:

        def warning(self, message):
            notes.append(message)

    entries = resolve_governors(section, ['chainA', {'alpha': ['on_return']}], [], _get_class_stages, logger=_Log())
    assert len(entries) == 1
    assert entries[0].stages == frozenset({'on_return'})
    assert len(notes) == 1 and 'overrides' in notes[0]


def test_omit_drops_a_config_name():
    """omit=['beta'] drops beta that my_chain brought in: alpha, gamma remain."""
    # omit matches by the name as written
    section = {'my_chain': ['alpha', 'beta']}
    entries = _resolve_governors(section, ['my_chain', 'gamma'], agent_omit=['beta'])
    assert [e.config_name for e in entries] == ['alpha', 'gamma']


def test_agent_entry_wins_over_chain_for_same_config_name():
    """Nearest wins: the agent's on_return replaces the chain's on_call."""
    section = {'my_chain': [{'alpha': ['on_call']}]}
    entries = _resolve_governors(section, ['my_chain', {'alpha': ['on_return']}])
    assert len(entries) == 1
    assert entries[0].stages == frozenset({'on_return'})


def test_parse_bare_and_mapping_forms():
    """A name and a one-key mapping parse; two keys or an int raise TokeoAiError."""
    assert parse_entry('alpha') == ('alpha', None)
    assert parse_entry({'alpha': ['on_call']}) == ('alpha', ['on_call'])
    with pytest.raises(TokeoAiError):
        parse_entry({'a': [], 'b': []})  # more than one key
    with pytest.raises(TokeoAiError):
        parse_entry(42)  # not a name or mapping


def test_parse_a_single_stage_as_a_list_of_one():
    """{alpha: on_call} parses like {alpha: [on_call]}."""
    assert parse_entry({'alpha': 'on_call'}) == ('alpha', ['on_call'])


# ---- tools: group expansion and cycle finding (config.tools) ----------------


def test_tools_bare_name_passes_through():
    """calc, which is not a group, passes through as an item."""
    assert resolve_tools(['calc'], {}) == ['calc']


def test_tools_group_expands_to_members():
    """The group math expands to its members in order: calc, stats."""
    groups = {'math': ['calc', 'stats']}
    assert resolve_tools(['math'], groups) == ['calc', 'stats']


def test_tools_groups_nest_and_dedupe():
    """A group in a group expands in order with duplicates dropped: a, b, c."""
    groups = {'inner': ['a', 'b'], 'outer': ['inner', 'b', 'c']}
    assert resolve_tools(['outer'], groups) == ['a', 'b', 'c']


def test_tools_cycle_is_broken_not_raised():
    """resolve_tools breaks the cycle a, b silently; find_cycles reports it."""
    groups = {'a': ['b'], 'b': ['a']}
    # no infinite loop, returns the reachable items
    resolve_tools(['a'], groups)


def test_find_cycles_reports_a_self_containing_group():
    """Groups a and b containing each other are reported, the group free is not."""
    groups = {'a': ['b'], 'b': ['a'], 'free': ['x']}
    cyclic = find_cycles(groups)
    assert 'a' in cyclic and 'b' in cyclic
    assert 'free' not in cyclic


def test_find_cycles_empty_when_acyclic():
    """An acyclic group mapping yields an empty set."""
    assert find_cycles({'g': ['a', 'b']}) == set()


# ---- sandboxes: containment and the chain walk (config.sandboxes) -----------


def _resolve_names(names):
    # a tool resolver stub: names pass through unchanged (no groups)
    return list(names or [])


def test_sandbox_has_no_all_keyword():
    """tools: _all is just a name, so a sandbox with it contains no 'anything'."""
    assert not sandbox_contains_tool({'tools': '_all'}, 'anything', _resolve_names)


def test_sandbox_listed_tool_is_contained():
    """A sandbox with tools: [calc] contains calc and not other."""
    assert sandbox_contains_tool({'tools': ['calc']}, 'calc', _resolve_names)
    assert not sandbox_contains_tool({'tools': ['calc']}, 'other', _resolve_names)


def test_sandbox_except_excludes_from_this_sandbox():
    """except: [calc] takes calc out of a [calc, other] sandbox, other stays in."""
    item = {'tools': ['calc', 'other'], 'except': ['calc']}
    assert not sandbox_contains_tool(item, 'calc', _resolve_names)
    assert sandbox_contains_tool(item, 'other', _resolve_names)


def test_sandbox_scalar_tools_and_except_are_one_name():
    """A scalar 'calc' in tools or except is one name, not iterated chars."""
    # as_list does that
    assert sandbox_contains_tool({'tools': 'calc'}, 'calc', _resolve_names)
    assert not sandbox_contains_tool({'tools': ['calc'], 'except': 'calc'}, 'calc', _resolve_names)


def test_sandbox_for_takes_first_matching_in_chain():
    """The first containing sandbox wins: calc in jailed, other in allow."""
    items = {'jailed': {'tools': ['calc']}, 'allow': {'tools': ['calc', 'other']}}
    # calc is in jailed (first); other falls through to allow
    assert sandbox_for('calc', ['jailed', 'allow'], items.get, _resolve_names) == 'jailed'
    assert sandbox_for('other', ['jailed', 'allow'], items.get, _resolve_names) == 'allow'


def test_sandbox_for_exhausted_chain_returns_none():
    """No sandbox in the chain contains other: sandbox_for returns None."""
    items = {'jailed': {'tools': ['calc']}}
    assert sandbox_for('other', ['jailed'], items.get, _resolve_names) is None


# ---- profiles: selection and agent binding (config.profiles) ----------------


_PROFILES = {
    'mock': {'type': 'mock', 'agent': 'guarded', 'purpose': 'mocking', 'model': 'm1'},
    'off': {'type': 'mock', 'enabled': False},
}


def test_profile_default_when_no_selector():
    """Without a selector the default profile mock is resolved."""
    assert resolve_profile(_PROFILES, 'mock')[0] == 'mock'


def test_profile_by_purpose_and_model():
    """purpose 'mocking' and model 'm1' each select mock."""
    assert resolve_profile(_PROFILES, None, purpose='mocking')[0] == 'mock'
    assert resolve_profile(_PROFILES, None, model='m1')[0] == 'mock'


def test_profile_more_than_one_selector_is_an_error():
    """Selecting by profile and model at once raises TokeoAiError."""
    with pytest.raises(TokeoAiError):
        resolve_profile(_PROFILES, None, profile='mock', model='m1')


def test_profile_no_selector_no_default_is_an_error():
    """No selector and no default profile raise TokeoAiError."""
    with pytest.raises(TokeoAiError):
        resolve_profile(_PROFILES, None)


def test_profile_disabled_is_not_found():
    """The disabled profile off is not found by its name: TokeoAiError."""
    with pytest.raises(TokeoAiError):
        find_profile(_PROFILES, 'profile', 'off')


def test_agent_name_call_argument_wins():
    """The call argument 'explicit' wins over the profile and the default."""
    assert resolve_agent_name('explicit', _PROFILES['mock'], 'default_x') == 'explicit'


def test_agent_name_from_profile_then_default():
    """Without a call argument the profile's guarded is used, else default_x."""
    assert resolve_agent_name(None, _PROFILES['mock'], 'default_x') == 'guarded'
    assert resolve_agent_name(None, {}, 'default_x') == 'default_x'


def test_agent_name_profile_null_opts_out():
    """An explicit agent: null on the profile opts out: None despite default_x."""
    assert resolve_agent_name(None, {'agent': None}, 'default_x') is None


# ---- CONFIG.md: the sandbox options as specified ------------------------------


def _format_yaml_default(value):
    # a default as CONFIG.md writes it in its tables
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return str(value)


@pytest.mark.parametrize(
    'heading, cls_path',
    [
        ('### 5.2 ```in_process```', 'tokeo.core.ai.sandboxes.in_process:TokeoAiInProcessSandbox'),
        ('### 5.3 ```subprocess```', 'tokeo.core.ai.sandboxes.subprocess:TokeoAiSubprocessSandbox'),
        ('### 5.4 ```docker```', 'tokeo.core.ai.sandboxes.docker:TokeoAiDockerSandbox'),
        ('### 5.5 wasm', 'tokeo.core.ai.sandboxes.wasm:TokeoAiWasmSandbox'),
    ],
)
def test_config_md_lists_every_sandbox_option_with_its_default(heading, cls_path):
    """CONFIG.md names exactly the options and defaults of each sandbox type."""
    import os
    import re
    import importlib

    module, _, name = cls_path.partition(':')
    cls = getattr(importlib.import_module(module), name)
    path = os.path.join(os.path.dirname(importlib.import_module('tokeo.core.ai.config').__file__), 'CONFIG.md')
    with open(path) as f:
        text = f.read()
    # the section of the type, up to the next heading of any level
    start = text.index(heading)
    section = text[start:]
    section = section[: min(i for i in (section.find('\n### '), section.find('\n## ')) if i > 0)]
    rows = re.findall(r'^\| ```(\w+)``` \| ```([^`]*)``` \|', section, flags=re.M)
    assert dict(rows) == {key: _format_yaml_default(value) for key, value in cls.Meta.config_defaults.items()}
