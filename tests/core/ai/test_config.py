"""
Tests for the config resolvers (tokeo/core/ai/config).

The config package holds the pure resolvers that turn the raw ai configuration
into the shapes the handler runs -- one module per component kind. These tests
pin each resolver's rules in isolation, without the handler or class loading
(stubs stand in for reflection and lookups):

- ```guards```: the §7/§8 composition notation -- bare names, ```name: [stages]```
  stage lists, ```_any```, chains, ```omit```, and the §8 conflict rules.
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


# a stub class-stage lookup: every identity can do these three stages, so the
# tests target the composition logic, not reflection
def _stages_of(identity):
    return ['on_prompt', 'on_call', 'on_return']


def _resolve(guards_section, agent_guards, agent_omit=None):
    return resolve_governors(guards_section, agent_guards, agent_omit or [], _stages_of)


def test_bare_name_runs_all_class_stages():
    # a bare name is additive: all the stages the class can do
    entries = _resolve({}, ['alpha'])
    assert len(entries) == 1
    assert entries[0].identity == 'alpha'
    assert entries[0].stages == frozenset({'on_prompt', 'on_call', 'on_return'})


def test_stage_list_runs_only_named_stages():
    # a {name: [stages]} entry is replacing: only the listed stages, intersected
    # with what the class can do
    entries = _resolve({}, [{'alpha': ['on_call']}])
    assert entries[0].stages == frozenset({'on_call'})


def test_any_token_means_all_class_stages():
    # [_any] is the explicit form of the bare name: all the class's stages
    entries = _resolve({}, [{'alpha': [GOVERNOR_STAGE_ANY]}])
    assert entries[0].stages == frozenset({'on_prompt', 'on_call', 'on_return'})


def test_named_stage_the_class_cannot_do_is_dropped():
    # a named stage the class does not implement is intersected away (the linter
    # flags it; the resolver does not block the run)
    entries = _resolve({}, [{'alpha': ['on_call', 'on_begin']}])
    assert entries[0].stages == frozenset({'on_call'})


def test_empty_stage_list_is_an_error():
    # an empty / null / [] stage list is too easily a mistake to read as intent
    with pytest.raises(TokeoAiError):
        _resolve({}, [{'alpha': []}])
    with pytest.raises(TokeoAiError):
        _resolve({}, [{'alpha': None}])


def test_order_is_first_appearance():
    # entries keep their first-appearance order
    entries = _resolve({}, ['alpha', 'beta', 'gamma'])
    assert [e.identity for e in entries] == ['alpha', 'beta', 'gamma']


def test_same_identity_same_stages_collapses_silently():
    # the same identity named twice with the SAME participation collapses to one
    entries = _resolve({}, [{'alpha': ['on_call']}, {'alpha': ['on_call']}])
    assert len(entries) == 1
    assert entries[0].stages == frozenset({'on_call'})


def test_same_identity_different_stages_same_level_is_error():
    # the same identity twice on one level with DIFFERENT stages is not
    # decidable -- to run it at several stages, name them in one stage list
    with pytest.raises(TokeoAiError):
        _resolve({}, [{'alpha': ['on_call']}, {'alpha': ['on_return']}])


def test_a_chain_is_expanded_in_place():
    # a list value under ai.guards is a chain; referencing it expands its members
    section = {'my_chain': ['alpha', {'beta': ['on_call']}]}
    entries = _resolve(section, ['my_chain', 'gamma'])
    assert [e.identity for e in entries] == ['alpha', 'beta', 'gamma']
    assert entries[1].stages == frozenset({'on_call'})


def test_a_chain_may_import_another_chain():
    section = {'inner': ['alpha'], 'outer': ['inner', 'beta']}
    entries = _resolve(section, ['outer'])
    assert [e.identity for e in entries] == ['alpha', 'beta']


def test_a_chain_cycle_is_an_error():
    section = {'a': ['b'], 'b': ['a']}
    with pytest.raises(TokeoAiError):
        _resolve(section, ['a'])


def test_two_chains_contradicting_is_an_error():
    # the same identity with different stages from two equal chains is not
    # decidable (no nearer form to rank them) -- an error
    section = {'chainA': [{'alpha': ['on_call']}], 'chainB': [{'alpha': ['on_return']}]}
    with pytest.raises(TokeoAiError):
        _resolve(section, ['chainA', 'chainB'])


def test_agent_over_chain_logs_a_note_and_agent_wins():
    # an agent's direct stage list overrides a chain's for the same identity
    # (nearest wins); a logger gets a note that the agent overrode the chain
    section = {'chainA': [{'alpha': ['on_call']}]}
    notes = []

    class _Log:

        def warning(self, message):
            notes.append(message)

    entries = resolve_governors(section, ['chainA', {'alpha': ['on_return']}], [], _stages_of, logger=_Log())
    assert len(entries) == 1
    assert entries[0].stages == frozenset({'on_return'})
    assert len(notes) == 1 and 'overrides' in notes[0]


def test_omit_drops_an_identity():
    # omit removes a named identity from the composition (e.g. one a chain
    # brought in), by the name as written
    section = {'my_chain': ['alpha', 'beta']}
    entries = _resolve(section, ['my_chain', 'gamma'], agent_omit=['beta'])
    assert [e.identity for e in entries] == ['alpha', 'gamma']


def test_agent_entry_wins_over_chain_for_same_identity():
    # nearest wins: an agent's direct stage list overrides a chain's for the
    # same identity (§8); the agent form replaces, not unions
    section = {'my_chain': [{'alpha': ['on_call']}]}
    entries = _resolve(section, ['my_chain', {'alpha': ['on_return']}])
    assert len(entries) == 1
    assert entries[0].stages == frozenset({'on_return'})


def test_parse_bare_and_mapping_forms():
    assert parse_entry('alpha') == ('alpha', None)
    assert parse_entry({'alpha': ['on_call']}) == ('alpha', ['on_call'])
    with pytest.raises(TokeoAiError):
        parse_entry({'a': [], 'b': []})  # more than one key
    with pytest.raises(TokeoAiError):
        parse_entry(42)  # not a name or mapping


# --- tools: group expansion and cycle finding (config.tools) ---


def test_tools_bare_name_passes_through():
    # a name that is not a group is an item, returned as-is
    assert resolve_tools(['calc'], {}) == ['calc']


def test_tools_group_expands_to_members():
    # a group name expands to its members, in order
    groups = {'math': ['calc', 'stats']}
    assert resolve_tools(['math'], groups) == ['calc', 'stats']


def test_tools_groups_nest_and_dedupe():
    # a group may contain a group; order is kept and duplicates dropped
    groups = {'inner': ['a', 'b'], 'outer': ['inner', 'b', 'c']}
    assert resolve_tools(['outer'], groups) == ['a', 'b', 'c']


def test_tools_cycle_is_broken_not_raised():
    # resolve_tools breaks a cyclic membership silently (find_cycles reports it)
    groups = {'a': ['b'], 'b': ['a']}
    # no infinite loop, returns the reachable items
    resolve_tools(['a'], groups)


def test_find_cycles_reports_a_self_containing_group():
    groups = {'a': ['b'], 'b': ['a'], 'free': ['x']}
    cyclic = find_cycles(groups)
    assert 'a' in cyclic and 'b' in cyclic
    assert 'free' not in cyclic


def test_find_cycles_empty_when_acyclic():
    assert find_cycles({'g': ['a', 'b']}) == set()


# --- sandboxes: containment and the chain walk (config.sandboxes) ---


def _resolve_passthrough(names):
    # a tool resolver stub: names pass through unchanged (no groups)
    return list(names or [])


def test_sandbox_all_keyword_contains_every_tool():
    assert sandbox_contains_tool({'tools': '_all'}, 'anything', _resolve_passthrough)


def test_sandbox_listed_tool_is_contained():
    assert sandbox_contains_tool({'tools': ['calc']}, 'calc', _resolve_passthrough)
    assert not sandbox_contains_tool({'tools': ['calc']}, 'other', _resolve_passthrough)


def test_sandbox_except_excludes_from_this_sandbox():
    item = {'tools': '_all', 'except': ['calc']}
    assert not sandbox_contains_tool(item, 'calc', _resolve_passthrough)
    assert sandbox_contains_tool(item, 'other', _resolve_passthrough)


def test_sandbox_scalar_tools_and_except_are_one_name():
    # a single name (a scalar) is one name, not iterated chars (as_list)
    assert sandbox_contains_tool({'tools': 'calc'}, 'calc', _resolve_passthrough)
    assert not sandbox_contains_tool({'tools': '_all', 'except': 'calc'}, 'calc', _resolve_passthrough)


def test_sandbox_for_takes_first_matching_in_chain():
    items = {'jailed': {'tools': ['calc']}, 'allow': {'tools': '_all'}}
    # calc is in jailed (first); other falls through to allow
    assert sandbox_for('calc', ['jailed', 'allow'], items.get, _resolve_passthrough) == 'jailed'
    assert sandbox_for('other', ['jailed', 'allow'], items.get, _resolve_passthrough) == 'allow'


def test_sandbox_for_exhausted_chain_returns_none():
    items = {'jailed': {'tools': ['calc']}}
    assert sandbox_for('other', ['jailed'], items.get, _resolve_passthrough) is None


# --- profiles: selection and agent binding (config.profiles) ---


_PROFILES = {
    'mock': {'type': 'mock', 'agent': 'guarded', 'purpose': 'mocking', 'options': {'model': 'm1'}},
    'off': {'type': 'mock', 'enabled': False},
}


def test_profile_default_when_no_selector():
    assert resolve_profile(_PROFILES, 'mock')[0] == 'mock'


def test_profile_by_purpose_and_model():
    assert resolve_profile(_PROFILES, None, purpose='mocking')[0] == 'mock'
    assert resolve_profile(_PROFILES, None, model='m1')[0] == 'mock'


def test_profile_more_than_one_selector_is_an_error():
    with pytest.raises(TokeoAiError):
        resolve_profile(_PROFILES, None, profile='mock', model='m1')


def test_profile_no_selector_no_default_is_an_error():
    with pytest.raises(TokeoAiError):
        resolve_profile(_PROFILES, None)


def test_profile_disabled_is_not_found():
    with pytest.raises(TokeoAiError):
        find_profile(_PROFILES, 'profile', 'off')


def test_agent_name_call_argument_wins():
    assert resolve_agent_name('explicit', _PROFILES['mock'], 'default_x') == 'explicit'


def test_agent_name_from_profile_then_default():
    assert resolve_agent_name(None, _PROFILES['mock'], 'default_x') == 'guarded'
    assert resolve_agent_name(None, {}, 'default_x') == 'default_x'


def test_agent_name_profile_null_opts_out():
    # an explicit agent: null on the profile opts out, overriding the default
    assert resolve_agent_name(None, {'agent': None}, 'default_x') is None
