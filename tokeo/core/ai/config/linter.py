"""
Linter for the ai extension configuration.

Lints the ```ai``` configuration in one place, in two passes: a form pass
(allowed keys and value kinds) and a reference pass (every ```type``` resolves on
```app.ai```, and every name a profile or group points at exists). Nothing is
raised; the caller decides what to do with the reported issues.

It runs once at setup on every path, in the ```post_setup``` hook
```ai_lint_on_setup``` after the project's own hooks, so a typo (a missing tool,
an unresolved ```type```) ends the start with the report; a warning goes to the
log. ```ai lint``` and ```--help``` skip it, so the report and the help stay
reachable. It is also exposed as the ```ai lint``` command, where ```--strict```
makes warnings fail that command.
"""

import difflib
from dataclasses import dataclass

from tokeo.core.utils.validate import as_list, is_int_ge
from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.guard import TokeoAiGuard
from tokeo.core.ai.transformer import TokeoAiTransformer
from tokeo.core.ai.conductor import TokeoAiConductor
from tokeo.core.ai.governor import TokeoAiGovernor, GOVERNOR_STAGES, GOVERNOR_STAGE_ANY
from tokeo.core.ai.config.governors import parse_entry, resolve_governors
from tokeo.core.ai.config.tools import find_cycles


# allowed keys per section, so an unknown key (a typo such as ```toolss```) is
# an error instead of being silently ignored. tools, agents, and guards share
# the uniform item form ({type, options}); the ```defaults``` block is checked
# here, agent option contents by the built-in element validators below.
# max_steps/max_loops are the handler's base loop limits (config_defaults),
# settable at the ai section level; trace toggles recording the step history
_AI_KEYS = {
    'license_acknowledged',
    'defaults',
    'profiles',
    'tools',
    'agents',
    'guards',
    'transformers',
    'conductors',
    'sandboxes',
    'max_steps',
    'max_loops',
    'trace',
}
_DEFAULTS_KEYS = {'profile', 'agent'}
_PROFILE_KEYS = {
    'type',
    'purpose',
    'agent',
    'deny',
    'enabled',
    'model',
    'model_params',
    'options',
}
_ITEM_KEYS = {'type', 'options'}
# a guard item also accepts a per-stage on_<stage> block (its own options
# override for that stage); agents and tools have no stages, so they do not
_GUARD_ITEM_KEYS = _ITEM_KEYS | set(GOVERNOR_STAGES)
# the three governor sections and, per section, the role base a configured type
# must derive from, so the linter keeps each section pure (a guard type only
# in ai.guards, a transformer only in ai.transformers, ...)
_GOVERNOR_SECTIONS = ('guards', 'transformers', 'conductors')
_GOVERNOR_ROLE_BASE = {'guards': TokeoAiGuard, 'transformers': TokeoAiTransformer, 'conductors': TokeoAiConductor}
_GOVERNOR_ROLE_NAME = {'guards': 'guard', 'transformers': 'transformer', 'conductors': 'conductor'}
_SANDBOX_KEYS = {'type', 'tools', 'except', 'options'}


@dataclass
class AiLintIssue:
    """
    A single ai configuration problem found by the linter.

    ### Args

    - **path** (str): The ```ai.<section>.<name>``` location of the problem
    - **message** (str): What is wrong, with a hint where one applies
    - **level** (str): ```error``` (breaks resolution, or a key nobody
        reads), ```warning``` (a gap worth closing, e.g. no defaults), or
        ```note``` (a resolved-but-worth-pointing-out fact, e.g. an agent
        overriding a chain)

    """

    path: str
    message: str
    level: str = 'error'


class TokeoAiLinter:
    """
    Lints the ```ai``` configuration against the live registries.

    ### Args

    - **app**: The application instance; its ```app.ai``` registries resolve
        every ```type```

    ### Notes

    - Construct it with the application, then call ```lint``` to get the issues
    - Every ```type``` is resolved through ```app.ai```, so a broken reference is
        caught here rather than on first use

    """

    def __init__(self, app):
        self.app = app
        self.issues = []
        self._validators = {}
        # built-in element validations; a project or a later derivation adds
        # its own checks the same way (for example for a custom guard's
        # options)
        self.add_validator('agents', self._validate_item_form)
        self.add_validator('agents', self._validate_agent_options)
        self.add_validator('guards', self._validate_governor_entry)
        self.add_validator('transformers', self._validate_governor_entry)
        self.add_validator('conductors', self._validate_governor_entry)
        self.add_validator('sandboxes', self._validate_sandbox)

    def add_validator(self, section, validator):
        """
        Register a validation call for the elements of one ```ai``` section.

        Every registered validator runs once per element of the section when
        ```lint``` walks it, so a subclass or a project extends the linter
        without touching its internals.

        ### Args

        - **section** (str): The ```ai``` section to validate (for example
            ```agents``` or ```guards```)
        - **validator** (callable): Called as ```validator(section, name,
            value)``` per element; it returns an iterable of ```AiLintIssue```
            entries (or ```None``` when it reports through the linter itself).
            It may also raise: a ```TokeoAiError``` becomes an error issue with
            its message at the element, any other exception is reported as a
            failed validator; the lint run itself never crashes

        """
        self._validators.setdefault(section, []).append(validator)

    def lint(self):
        """
        Lint the ```ai``` configuration and return the issues found.

        ### Returns

        - **list**: ```AiLintIssue``` entries; an empty list means the
            configuration is sound

        """
        self.issues = []
        tools = self._value('tools') or {}
        profiles = self._value('profiles') or {}
        defaults = self._value('defaults')
        agents = self._value('agents') or {}
        self._lint_keys('ai', self._section_keys(), _AI_KEYS)
        self._lint_max_values('ai', {key: self._value(key) for key in ('max_steps', 'max_loops')})
        trace = self._value('trace')
        if trace is not None and not isinstance(trace, bool):
            self._add('ai.trace', 'must be true or false')
        # required: someone must have set it, any value counts
        if 'license_acknowledged' not in self._section_keys():
            self._add('ai.license_acknowledged', 'is required, set it to true once you have read LICENSE.md of tokeo-fundi')
        self._lint_tools(tools)
        self._lint_profiles(profiles, tools)
        self._lint_defaults(defaults, profiles, agents)
        self._lint_governor_uniqueness()
        for section in self._validators:
            self._run_validators(section)
        return self.issues

    def _run_validators(self, section):
        # run the registered validators over every element of one section; a
        # non-mapping section is reported once instead of being walked
        entries = self._value(section) or {}
        if not isinstance(entries, dict):
            self._add(f'ai.{section}', 'must be a mapping of name to item')
            return
        for name, value in entries.items():
            for validator in self._validators.get(section, []):
                # a raising validator never crashes the lint run: a
                # TokeoAiError is a deliberate decision and becomes an error
                # issue with its message, any other exception is an error in
                # the validator itself and is reported as such
                try:
                    issues = validator(section, name, value)
                except TokeoAiError as err:
                    self._add(f'ai.{section}.{name}', str(err))
                    continue
                except Exception as err:
                    self._add(f'ai.{section}.{name}', f'validator failed: {type(err).__name__}: {err}')
                    continue
                self.issues.extend(issues or [])

    def _validate_governor_entry(self, section, name, value):
        # an ai.guards entry is distinguished by the form of its value:
        # a mapping is an item (type, options, the six per-stage blocks); a
        # string is the short form name: module:X (= {type: module:X}); a list
        # is a chain (a composition, validated as such). guards and chains share
        # one namespace; a name is one or the other, never both
        path = f'ai.{section}.{name}'
        if isinstance(value, str):
            # short form: the string is the type; resolve it like an item
            self._lint_type('governor', path, {'type': value})
            self._lint_governor_purity(section, path, value)
        elif isinstance(value, list):
            # a chain: a composition, the same notation as an agent's governors list
            self._lint_guard_composition(path, value, [name])
        elif isinstance(value, dict):
            # an item: the keys of every item plus the per-stage blocks
            self._lint_keys(path, value, _GUARD_ITEM_KEYS)
            self._lint_options(path, value)
            self._lint_type('governor', path, value)
            self._lint_option_keys('governor', f'{path}.options', value.get('type'), value.get('options'))
            self._validate_options_via_class('governor', path, value)
            # an on_<stage> block holds only its own options for that stage
            for stage in GOVERNOR_STAGES:
                block = value.get(stage)
                if isinstance(block, dict):
                    self._lint_keys(f'{path}.{stage}', block, {'options'})
                    self._lint_options(f'{path}.{stage}', block)
                    self._lint_option_keys('governor', f'{path}.{stage}.options', value.get('type'), block.get('options'))
                    stage_entry = dict(type=value.get('type'), options=block.get('options'))
                    self._validate_options_via_class('governor', f'{path}.{stage}', stage_entry)
                elif block is not None:
                    self._add(f'{path}.{stage}', 'must be a mapping with "options"')
            self._lint_governor_purity(section, path, value.get('type'))
            self._lint_tool_name_options(path, value)
        else:
            self._add(path, 'must be an item (mapping), a short form (string), or a chain (list)')

    def _governors_merged(self):
        # the one governor registry the handler builds at load: the three
        # sections merged by name. a name duplicated across sections is reported
        # by _lint_governor_uniqueness; here a plain merge resolves references
        merged = {}
        for section in _GOVERNOR_SECTIONS:
            merged.update(self._value(section) or {})
        return merged

    def _lint_governor_uniqueness(self):
        # a governor name must be unique across guards/transformers/conductors
        # (they merge into one registry). report a name that appears in more
        # than one section, so the collision is caught before the merge
        seen = {}
        for section in _GOVERNOR_SECTIONS:
            for name in self._value(section) or {}:
                seen.setdefault(name, []).append(section)
        for name, sections in seen.items():
            if len(sections) > 1:
                where = ', '.join(sections)
                self._add(
                    f'ai.{sections[-1]}.{name}',
                    f'governor name {name!r} is configured in more than one section ({where}); '
                    'names are unique across guards/transformers/conductors',
                )

    def _lint_governor_purity(self, section, path, type_value):
        # section purity: a type in a section must resolve to a class deriving
        # from that section's role base (guards -> TokeoAiGuard, ...). the handler
        # resolves uniformly under one 'governor' kind, so this is the only place
        # the guard/transformer/conductor distinction is enforced at build time
        if not isinstance(type_value, str):
            return
        try:
            cls = self.app.ai.resolve('governor', type_value)
        except TokeoAiError:
            # an unresolved type is already reported by _lint_type
            return
        expected = _GOVERNOR_ROLE_BASE.get(section)
        if expected is None or not isinstance(cls, type):
            return
        if not issubclass(cls, expected):
            class_path = f'{cls.__module__}:{cls.__qualname__}'
            self._add(path, f'class {class_path} can not be used as {_GOVERNOR_ROLE_NAME[section]}')

    def _lint_tool_name_options(self, path, item):
        # a governor class that decides by tool names declares which of its
        # options hold them (```Meta.tool_name_options```); each name there must
        # be a tool or group of ai.tools, a typo would never match a call
        options = item.get('options')
        if not isinstance(options, dict):
            return
        try:
            cls = self.app.ai.resolve('governor', item.get('type'))
        except TokeoAiError:
            return
        meta = getattr(cls, 'Meta', None)
        tool_names = set(self._value('tools') or {})
        for key in getattr(meta, 'tool_name_options', ()):
            self._lint_names(f'{path}.options.{key}', options.get(key), tool_names, 'tool or group')

    def _validate_item_form(self, section, name, item):
        # the item form of an agent: a mapping with a resolvable ```type``` and
        # the component's own options under ```options```; the keys inside
        # options are the component's Meta keys and stay unchecked here (a
        # custom class sets its own). an agent has no stages, so it keeps the
        # plain item keys; governors are checked by _validate_governor_entry
        path = f'ai.{section}.{name}'
        if not self._lint_long_form(path, item, 'agent'):
            return
        self._lint_keys(path, item, _ITEM_KEYS)
        self._lint_options(path, item)
        # the registry kind is the singular section name (agents -> agent)
        self._lint_type(section.rstrip('s'), path, item)
        self._lint_option_keys(section.rstrip('s'), f'{path}.options', item.get('type'), item.get('options'))

    def _validate_agent_options(self, section, name, item):
        path = f'ai.{section}.{name}'
        # a yaml key null is None; a leading __ belongs to the framework
        if not isinstance(name, str):
            self._add(path, 'an agent name must be a string')
            return
        if name.startswith('__'):
            self._add(path, 'a name starting with __ is reserved for the framework, choose another name')
            return
        if not isinstance(item, dict):
            return
        options = item.get('options')
        path = f'{path}.options'
        # governors are required, [] is a deliberate choice
        if not isinstance(options, dict) or 'governors' not in options:
            self._add(f'{path}.governors', 'must list governors (an empty list is allowed)')
        elif options.get('governors') == []:
            self._add(f'{path}.governors', 'is empty, the agent runs without governors', level='note')
        if not isinstance(options, dict):
            return
        tool_names = set(self._value('tools') or {})
        self._lint_names(f'{path}.tools', options.get('tools'), tool_names, 'tool or group')
        # the guards composition: each entry is a bare name or a one-key
        # {name: [stages]} mapping; chains (a list value under ai.guards) may be
        # referenced too. omit is a sibling field, not a list entry
        self._lint_guard_composition(f'{path}.governors', options.get('governors'))
        self._lint_guard_omit(f'{path}.omit', options.get('omit'))
        self._lint_max_values(path, options)
        self._lint_names(f'{path}.sandboxes', options.get('sandboxes'), set(self._value('sandboxes') or {}), 'sandbox')
        self._lint_names(f'{path}.deny', options.get('deny'), tool_names, 'tool or group')
        # union/conflict: run the one resolver so the linter reports exactly
        # what a run would do. a not-decidable conflict raises (caught as an
        # error issue by the validator runner); an agent-over-chain override
        # logs a note via this adapter
        self._lint_guard_resolution(path, options)

    def _lint_max_values(self, path, values):
        # at ai level or on an agent
        for key in ('max_steps', 'max_loops'):
            value = values.get(key)
            if value is not None and not is_int_ge(value, 0):
                self._add(f'{path}.{key}', 'must be a whole number of 0 or more (0 = unlimited)')

    def _lint_guard_resolution(self, path, options):
        # drive the resolver with a note-collecting logger, so the override
        # notes (agent wins over chain) become note issues and a not-decidable
        # conflict becomes an error issue. a resolution that cannot even build
        # (an unknown name, already reported by the form check) is skipped here
        notes = self

        class _NoteLogger:

            def warning(self, message):
                notes._add(f'{path}.governors', message, level='note')

        try:
            resolve_governors(
                self._governors_merged(),
                as_list(options.get('governors')),
                as_list(options.get('omit')),
                self._guard_class_stages_or_empty,
                logger=_NoteLogger(),
            )
        except TokeoAiError as err:
            # a not-decidable conflict (or a chain cycle): an error issue
            self._add(f'{path}.governors', str(err))

    def _guard_class_stages_or_empty(self, config_name):
        # the resolver's stages_of: the class stages, or empty when the class
        # cannot be resolved (the unknown name is reported by the form check, so
        # here an empty set just yields an empty participation, no second error)
        stages = self._guard_class_stages(config_name)
        return stages or set()

    def _guard_class_stages(self, config_name):
        # the stages a governor's class can do, by reflection over the resolved
        # class (its on_* methods vs the base). an alias resolves through its
        # ai.guards entry type; a class path resolves directly. returns
        # None when the class cannot be resolved (a separate error is reported)
        try:
            if ':' in config_name:
                cls = self.app.ai.resolve('governor', config_name)
            else:
                governor_entry = self._governors_merged().get(config_name)
                type_value = governor_entry.get('type') if isinstance(governor_entry, dict) else governor_entry
                if not isinstance(type_value, str):
                    return None
                cls = self.app.ai.resolve('governor', type_value)
        except TokeoAiError:
            return None
        return {stage for stage in GOVERNOR_STAGES if getattr(cls, stage) is not getattr(TokeoAiGovernor, stage)}

    def _lint_guard_composition(self, path, guards, chain_path=None):
        # validate a guards composition list: each entry is a bare name
        # or a one-key {name: [stages]}; a referenced chain (list value under
        # ai.guards) is walked too. a named stage must be one the guard's class
        # can do; an unknown alias (not a class path, not configured) is an
        # error; an empty/null/[] stage list is an error
        known = set(self._governors_merged())
        chain_path = chain_path or []
        for entry in as_list(guards):
            try:
                config_name, stages = parse_entry(entry)
            except TokeoAiError as err:
                self._add(path, str(err))
                continue
            value = self._governors_merged().get(config_name) if isinstance(config_name, str) else None
            # a list value under ai.guards is a chain: walk it (a chain entry
            # carries no stage list of its own)
            if isinstance(value, list):
                if stages is not None:
                    self._add(path, f'chain {config_name!r} cannot take a stage list; stages belong to its guards')
                    continue
                if config_name in chain_path:
                    self._add(path, f'guard chain {config_name!r} imports itself (cycle)')
                    continue
                self._lint_guard_composition(path, value, chain_path + [config_name])
                continue
            # a config name must be a configured name or a class path
            if '.' not in config_name and config_name not in known:
                self._add(path, _unknown('governor', config_name, known))
                continue
            # a named stage must be one the class can do
            if stages is not None:
                class_stages = self._guard_class_stages(config_name)
                if class_stages is not None:
                    for stage in stages:
                        if stage != GOVERNOR_STAGE_ANY and stage not in class_stages:
                            self._add(path, f'guard {config_name!r} cannot run at stage {stage!r} (its class does not)')

    def _lint_guard_omit(self, path, omit):
        # omit is a list of governor config names to drop from the composition;
        # it is a sibling field, not a list entry, so it never collides with a
        # guard or chain name. each name should be a known guard or chain
        known = set(self._governors_merged())
        for entry in as_list(omit):
            if not isinstance(entry, str):
                self._add(path, f'omit entry must be a guard name, got {entry!r}')
            elif '.' not in entry and entry not in known:
                self._add(path, _unknown('governor', entry, known))

    def _validate_sandbox(self, section, name, item):
        # option values are checked by the class in validate_options
        path = f'ai.{section}.{name}'
        if not self._lint_long_form(path, item, 'sandbox'):
            return
        self._lint_keys(path, item, _SANDBOX_KEYS)
        self._lint_options(path, item)
        self._lint_type('sandbox', path, item)
        self._lint_option_keys('sandbox', f'{path}.options', item.get('type'), item.get('options'))
        tool_names = set(self._value('tools') or {})
        # tools is required: the tool/group names from ai.tools; there is no
        # catch-all, a new tool runs nowhere until a sandbox names it
        listed = item.get('tools')
        if listed is None:
            self._add(f'{path}.tools', 'is required (a list of tool/group names)')
        elif '_all' in as_list(listed):
            self._add(f'{path}.tools', "has no catch-all '_all', name the tools and groups it runs")
        else:
            self._lint_names(f'{path}.tools', listed, tool_names, 'tool or group')
        # except excludes members from THIS sandbox only (single or list)
        self._lint_names(f'{path}.except', item.get('except'), tool_names, 'tool or group')
        # ask the resolved class to validate its own option keys
        self._validate_options_via_class('sandbox', path, item)

    def _validate_options_via_class(self, kind, path, item):
        # the class knows its allowed option keys; resolve it and call its
        # ```validate_options``` hook. a resolution failure is already reported
        # by ```_lint_type```; a hook that returns messages becomes lint errors
        options = item.get('options')
        if not isinstance(options, dict):
            return
        try:
            cls = self.app.ai.resolve(kind, item.get('type'))
        except Exception:
            return
        hook = getattr(cls, 'validate_options', None)
        if hook is None:
            return
        try:
            # the hook is an instance method on the base; call it unbound with
            # a None self, since the built-in checks only read the argument
            errors = hook(None, options)
        except Exception:
            return
        for message in errors or []:
            self._add(f'{path}.options', message)

    def _lint_names(self, path, selection, known, what):
        # a single name or a list of names from ```known```; None is allowed
        # (the field is absent). used for the agent ```deny``` and a sandbox
        # ```except```/```tools```, which all accept one entry or many
        for entry in as_list(selection):
            if entry not in known:
                self._add(path, _unknown(what, entry, known))

    def _add(self, path, message, level='error'):
        self.issues.append(AiLintIssue(path, message, level))

    def _value(self, key):
        # read one ```ai``` config value; a missing key raises in cement, so
        # swallow that and treat it as unset
        try:
            return self.app.config.get('ai', key)
        except Exception:
            return None

    def _section_keys(self):
        # the top-level keys present in the ```ai``` section, to spot typos
        try:
            return list(self.app.config.keys('ai'))
        except Exception:
            return []

    def _lint_keys(self, path, keys, allowed, what='key'):
        # a key nobody reads is a typo, never silently ignored
        for key in keys:
            if key not in allowed:
                self._add(f'{path}.{key}', _unknown(what, key, allowed))

    def _lint_option_keys(self, kind, path, type_value, options):
        # the allowed options are the config_defaults of the nearest Meta in
        # the MRO declaring them; a class without any is not checked
        if not isinstance(options, dict) or not isinstance(type_value, str):
            return
        try:
            cls = self.app.ai.resolve(kind, type_value)
        except TokeoAiError:
            return
        for klass in getattr(cls, '__mro__', ()):
            meta = vars(klass).get('Meta')
            if meta is not None and 'config_defaults' in vars(meta):
                self._lint_keys(path, options, set(meta.config_defaults or {}), 'option')
                return

    def _lint_long_form(self, path, item, what):
        # sandboxes, agents and profiles have no short form
        if isinstance(item, dict):
            return True
        if isinstance(item, str):
            self._add(path, f'a {what} has no short form, write it as a mapping: {{type: {item}, ...}}')
        else:
            self._add(path, f'a {what} must be a mapping with a "type"')
        return False

    def _lint_options(self, path, item):
        # ```options``` may be omitted or {}, null is an error
        if 'options' in item and not isinstance(item['options'], dict):
            self._add(f'{path}.options', 'must be a mapping, write {} or omit it')

    def _lint_type(self, kind, path, item):
        # every item names a class by ```type```; resolving it on ```app.ai```
        # imports a class path or looks up a built-in alias
        type_value = item.get('type')
        if not type_value:
            self._add(path, f'missing {kind} "type"')
            return
        if not isinstance(type_value, str):
            self._add(f'{path}.type', 'must be an alias or a class path module:Class')
            return
        try:
            self.app.ai.resolve(kind, type_value)
        except TokeoAiError as err:
            self._add(f'{path}.type', str(err))

    def _lint_tools(self, tools):
        if not isinstance(tools, dict):
            if tools:
                self._add('ai.tools', 'must be a mapping of name to item or group')
            return
        # a dict is an item, a string its short form, a list a group
        items, groups = {}, {}
        for name, value in tools.items():
            if isinstance(value, list):
                groups[name] = value
            elif isinstance(value, str):
                items[name] = dict(type=value)
            elif isinstance(value, dict):
                items[name] = value
            else:
                self._add(f'ai.tools.{name}', 'must be an item (mapping), a short form (string), or a group (list)')
        for name, item in items.items():
            path = f'ai.tools.{name}'
            self._lint_keys(path, item, _ITEM_KEYS)
            self._lint_options(path, item)
            self._lint_type('tool', path, item)
            self._lint_option_keys('tool', f'{path}.options', item.get('type'), item.get('options'))
        known = set(items) | set(groups)
        for name, entries in groups.items():
            path = f'ai.tools.{name}'
            if not all(isinstance(entry, str) for entry in entries):
                self._add(path, 'a group must be a list of tool or group names')
                continue
            for entry in entries:
                if entry not in known:
                    self._add(path, _unknown('tool or group', entry, known))
        self._lint_cycles(groups)

    def _lint_cycles(self, groups):
        # report a group that transitively contains itself; without this a
        # cycle would just be silently broken at resolve time. the cycle finding
        # is the shared one (config.tools.find_cycles), so the linter and the
        # handler's expansion agree on what a cycle is
        for group_name in sorted(find_cycles(groups)):
            self._add(f'ai.tools.{group_name}', 'group has a cyclic membership')

    def _lint_profiles(self, profiles, tools):
        if not isinstance(profiles, dict):
            if profiles:
                self._add('ai.profiles', 'must be a mapping of name to profile')
            return
        agent_names = set(self._value('agents') or {})
        tool_names = set(tools) if isinstance(tools, dict) else set()
        for name, profile in profiles.items():
            path = f'ai.profiles.{name}'
            if not self._lint_long_form(path, profile, 'profile'):
                continue
            self._lint_keys(path, profile, _PROFILE_KEYS)
            self._lint_options(path, profile)
            self._lint_type('provider', path, profile)
            # model_params is passed through unchecked
            self._lint_option_keys('provider', f'{path}.options', profile.get('type'), profile.get('options'))
            if profile.get('model_params') is not None and not isinstance(profile['model_params'], dict):
                self._add(f'{path}.model_params', 'must be a mapping')
            if 'enabled' in profile and not isinstance(profile['enabled'], bool):
                self._add(f'{path}.enabled', 'must be true or false')
            # a profile binds the agent (composition); null opts out on
            # purpose, a name must exist. a profile may ```deny``` tools/groups
            # to carve out its own subset of a shared agent's tools
            agent = profile.get('agent')
            if agent is not None and agent not in agent_names:
                self._add(f'{path}.agent', _unknown('agent', agent, agent_names))
            self._lint_names(f'{path}.deny', profile.get('deny'), tool_names, 'tool or group')

    def _lint_defaults(self, defaults, profiles, agents):
        # the ```defaults``` block names the profile (model) and the agent
        # (composition) used when a call selects none. both are optional, but
        # when set must name an existing entry; a missing block is a warning
        if not defaults:
            self._add('ai.defaults', 'no default profile or agent configured', 'warning')
            return
        if not isinstance(defaults, dict):
            self._add('ai.defaults', 'must be a mapping')
            return
        self._lint_keys('ai.defaults', defaults, _DEFAULTS_KEYS)
        profile = defaults.get('profile')
        if profile:
            known = set(profiles) if isinstance(profiles, dict) else set()
            if profile not in known:
                self._add('ai.defaults.profile', _unknown('profile', profile, known))
        agent = defaults.get('agent')
        if agent:
            known = set(agents) if isinstance(agents, dict) else set()
            if agent not in known:
                self._add('ai.defaults.agent', _unknown('agent', agent, known))


def _unknown(what, name, known):
    # build an "unknown X 'name'; did you mean 'closest'?" message
    match = difflib.get_close_matches(str(name), [str(k) for k in known], n=1)
    suggestion = f'; did you mean {match[0]!r}?' if match else ''
    return f'unknown {what} {name!r}{suggestion}'
