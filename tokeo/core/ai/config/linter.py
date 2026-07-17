"""
Linter for the ai extension configuration.

Lints the ```ai``` configuration in one place, in two passes: a form pass
(allowed keys and value kinds) and a reference pass (every ```type``` resolves on
```app.ai```, and every name a profile or group points at exists). Nothing is
raised; the caller decides what to do with the reported issues.

It runs automatically when ```app.ai``` is set up, so a typo (a missing tool, an
unresolved ```type```) fails fast at startup, and is also exposed as the
```ai lint``` command, where ```--strict``` turns warnings into failures.
"""

import difflib
from dataclasses import dataclass

from tokeo.core.utils.base import as_list
from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.guard import TokeoAiGuard
from tokeo.core.ai.transformer import TokeoAiTransformer
from tokeo.core.ai.conductor import TokeoAiConductor
from tokeo.core.ai.governor import TokeoAiGovernor, GOVERNOR_STAGES, GOVERNOR_STAGE_ANY
from tokeo.core.ai.config.governors import parse_entry, resolve_governors
from tokeo.core.ai.config.tools import find_cycles


# allowed keys per section, so an unknown key (a typo such as ```toolss```) is
# reported instead of being silently ignored. tools, agents, and guards share
# the uniform item form ({type, options}); the ```defaults``` block is checked
# here, agent option contents by the built-in element validators below.
# max_steps/max_loops are the handler's base loop budgets (config_defaults),
# settable at the ai section level; trace toggles recording the step history
_AI_KEYS = {
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
    'native_tools_call',
    'tools_parser',
    'model_params',
    'options',
}
_ITEM_KEYS = {'type', 'options'}
# a guard item also accepts a per-stage on_<stage> block (its own options
# override for that station); agents and tools have no stages, so they do not
_GUARD_ITEM_KEYS = _ITEM_KEYS | set(GOVERNOR_STAGES)
# the three governor sections and, per section, the role base a declared type
# must derive from -- so the linter keeps each section pure (a guard type only
# in ai.guards, a transformer only in ai.transformers, ...)
_GOVERNOR_SECTIONS = ('guards', 'transformers', 'conductors')
_ROLE_BASE = {'guards': TokeoAiGuard, 'transformers': TokeoAiTransformer, 'conductors': TokeoAiConductor}
_ROLE_NAME = {'guards': 'guard', 'transformers': 'transformer', 'conductors': 'conductor'}
_SANDBOX_KEYS = {'type', 'tools', 'except', 'options'}


@dataclass
class AiLintIssue:
    """
    A single ai configuration problem found by the linter.

    ### Notes

    - **path** (str): The ```ai.<section>.<name>``` location of the problem
    - **message** (str): What is wrong, with a hint where one applies
    - **level** (str): ```error``` (breaks resolution), ```warning``` (an
        ignored value, usually a typo), or ```note``` (a resolved-but-worth-
        pointing-out fact, e.g. an agent overriding a chain)

    """

    path: str
    message: str
    level: str = 'error'


class TokeoAiLinter:
    """
    Lints the ```ai``` configuration against the live registries.

    ### Notes

    - Construct it with the application, then call ```lint``` to get the issues
    - Every ```type``` is resolved through ```app.ai```, so a broken reference is
        caught here rather than on first use

    """

    def __init__(self, app):
        """
        Bind the linter to an application.

        ### Args

        - **app**: The application instance; its ```app.ai``` registries resolve
            every ```type```

        """
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
            failed validator -- the lint run itself never crashes

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
        elements = self._value(section) or {}
        if not isinstance(elements, dict):
            self._add(f'ai.{section}', 'must be a mapping of name to item')
            return
        for name, value in elements.items():
            for validator in self._validators.get(section, []):
                # a raising validator never crashes the lint run: a
                # TokeoAiError is a deliberate verdict and becomes an error
                # issue with its message, any other exception is a fault in
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
        # a map is a declaration (type, options, the six per-stage blocks); a
        # string is the short form name: module.X (= {type: module.X}); a list
        # is a chain (a composition, validated as such). guards and chains share
        # one namespace -- a name is one or the other, never both
        path = f'ai.{section}.{name}'
        if isinstance(value, str):
            # short form: the string is the type; resolve it like a declaration
            self._lint_type('governor', path, {'type': value})
            self._lint_governor_purity(section, path, value)
        elif isinstance(value, list):
            # a chain: a composition, the same notation as an agent's governors list
            self._lint_guard_composition(path, value, [name])
        elif isinstance(value, dict):
            # a declaration: the uniform item form plus the per-stage blocks
            self._lint_keys(path, value, _GUARD_ITEM_KEYS)
            self._lint_options(path, value)
            self._lint_type('governor', path, value)
            self._lint_governor_purity(section, path, value.get('type'))
        else:
            self._add(path, 'must be a declaration (map), a short form (string), or a chain (list)')

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
                    f'governor name {name!r} is declared in more than one section ({where}); '
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
        expected = _ROLE_BASE.get(section)
        if expected is None or not isinstance(cls, type):
            return
        if not issubclass(cls, expected):
            where = f'{cls.__module__}.{cls.__qualname__}'
            self._add(path, f'class {where} can not be used as {_ROLE_NAME[section]}')

    def _validate_item_form(self, section, name, item):
        # the uniform item form shared by agents and guards: a mapping with a
        # resolvable ```type``` and the component's own settings under
        # ```options```; settings keys inside options are the component's Meta
        # keys and stay unchecked here (a custom class declares its own)
        path = f'ai.{section}.{name}'
        if not isinstance(item, dict):
            self._add(path, 'must be an item (mapping with a "type")')
            return
        # guards additionally accept a per-stage on_<stage> block (its own
        # ```options``` override for that station, read by the guard's _config);
        # agents and tools have no stages, so they keep the plain item keys
        allowed = _GUARD_ITEM_KEYS if section == 'guards' else _ITEM_KEYS
        self._lint_keys(path, item, allowed)
        self._lint_options(path, item)
        # the registry kind is the singular section name (agents -> agent)
        self._lint_type(section.rstrip('s'), path, item)

    def _validate_agent_options(self, section, name, item):
        # the base agent's known option keys: the tools selection points into
        # ```ai.tools```, the guards selection into ```ai.guards```, and the
        # budgets are numbers; other keys may be a custom agent's own Meta keys
        if not isinstance(item, dict):
            return
        options = item.get('options')
        if not isinstance(options, dict):
            return
        path = f'ai.{section}.{name}'
        tool_names = set(self._value('tools') or {})
        self._lint_selection(path, options.get('tools'), tool_names)
        # the guards composition: each entry is a bare name or a one-key
        # {name: [stages]} mapping; chains (a list value under ai.guards) may be
        # referenced too. omit is a sibling field, not a list entry
        self._lint_guard_composition(f'{path}.governors', options.get('governors'))
        self._lint_guard_omit(f'{path}.omit', options.get('omit'))
        for key in ('max_steps', 'max_loops'):
            value = options.get(key)
            if value is not None and not isinstance(value, int):
                self._add(f'{path}.{key}', 'must be a number (0 = unlimited)')
        # the sandbox chain references ```ai.sandboxes``` by name, in order; an
        # empty or absent chain means no sandbox lists a tool -> denied
        sandboxes = options.get('sandboxes')
        if sandboxes is not None:
            if not isinstance(sandboxes, list):
                self._add(f'{path}.sandboxes', 'must be a list of sandbox names')
            else:
                known = set(self._value('sandboxes') or {})
                for entry in sandboxes:
                    if entry not in known:
                        self._add(f'{path}.sandboxes', _unknown('sandbox', entry, known))
        # deny is a hard exclusion: a single tool/group name or a list of them
        self._lint_names(f'{path}.deny', options.get('deny'), tool_names, 'tool or group')
        # union/conflict: run the one resolver so the linter reports exactly
        # what a run would do. a not-decidable conflict raises (caught as an
        # error issue by the validator runner); an agent-over-chain override
        # logs a note via this adapter
        self._lint_guard_resolution(path, options)

    def _lint_guard_resolution(self, path, options):
        # drive the resolver with a note-collecting logger, so the override
        # notes (agent wins over chain) become note issues and a not-decidable
        # conflict becomes an error issue. a resolution that cannot even build
        # (an unknown name, already reported by the form check) is skipped here
        governors = options.get('governors')
        if not isinstance(governors, list):
            return

        notes = self

        class _NoteLogger:

            def warning(self, message):
                notes._add(f'{path}.governors', message, level='note')

        try:
            resolve_governors(
                self._governors_merged(),
                governors,
                options.get('omit') or [],
                self._guard_class_stages_or_empty,
                logger=_NoteLogger(),
            )
        except TokeoAiError as err:
            # a not-decidable conflict (or a chain cycle) -- an error issue
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
        # ai.guards declaration type; a dotted class resolves directly. returns
        # None when the class cannot be resolved (a separate error is reported)
        try:
            if '.' in config_name:
                cls = self.app.ai.resolve('governor', config_name)
            else:
                declaration = self._governors_merged().get(config_name)
                type_value = declaration.get('type') if isinstance(declaration, dict) else declaration
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
        # can do; an unknown alias (not a dotted class, not declared) is an
        # error; an empty/null/[] stage list is an error
        if guards is None:
            return
        if not isinstance(guards, list):
            self._add(path, 'must be a list of guard names')
            return
        known = set(self._governors_merged())
        chain_path = chain_path or []
        for entry in guards:
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
            # a config name must be a declared name or a dotted class
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
        if omit is None:
            return
        if not isinstance(omit, list):
            self._add(path, 'must be a list of guard names')
            return
        known = set(self._governors_merged())
        for entry in omit:
            if not isinstance(entry, str):
                self._add(path, f'omit entry must be a guard name, got {entry!r}')
            elif '.' not in entry and entry not in known:
                self._add(path, _unknown('governor', entry, known))

    def _validate_sandbox(self, section, name, item):
        # a sandbox item: a resolvable ```type```, the required ```tools```
        # selection (the keyword ```_all``` or tool/group names), an
        # optional ```except``` skip set (single or list), and the class's own
        # ```options```. the option keys are validated by the class itself
        # through ```validate_options``` -- the linter does not know them
        path = f'ai.{section}.{name}'
        if not isinstance(item, dict):
            self._add(path, 'must be an item (mapping with a "type")')
            return
        self._lint_keys(path, item, _SANDBOX_KEYS)
        self._lint_type('sandbox', path, item)
        tool_names = set(self._value('tools') or {})
        # tools is required: either the reserved keyword _all (every
        # tool that reaches it) or a list of tool/group names from ai.tools
        listed = item.get('tools')
        if listed is None:
            self._add(f'{path}.tools', 'is required (a list of tool/group names, or _all)')
        elif listed != '_all':
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

    def _lint_keys(self, path, keys, allowed, level='warning'):
        # report any key outside the allowed set, with a closest-match hint
        for key in keys:
            if key not in allowed:
                self._add(f'{path}.{key}', _unknown('key', key, allowed), level)

    def _lint_options(self, path, item):
        # ```options``` is optional, but when present it carries the component's
        # own settings and must be a mapping
        options = item.get('options')
        if options is not None and not isinstance(options, dict):
            self._add(f'{path}.options', 'must be a mapping')

    def _lint_type(self, kind, path, item):
        # every item names a class by ```type```; resolving it on ```app.ai```
        # imports a dotted path or looks up a built-in alias
        type_value = item.get('type')
        if not type_value:
            self._add(path, f'missing {kind} "type"')
            return
        if not isinstance(type_value, str):
            self._add(f'{path}.type', 'must be an alias or a dotted path')
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
        # a dict value is an item, a list value is a group; else it is wrong
        items, groups = {}, {}
        for name, value in tools.items():
            if isinstance(value, list):
                groups[name] = value
            elif isinstance(value, dict):
                items[name] = value
            else:
                self._add(f'ai.tools.{name}', 'must be an item (mapping) or a group (list)')
        for name, item in items.items():
            path = f'ai.tools.{name}'
            self._lint_keys(path, item, _ITEM_KEYS)
            self._lint_options(path, item)
            self._lint_type('tool', path, item)
        known = set(items) | set(groups)
        for name, members in groups.items():
            path = f'ai.tools.{name}'
            if not all(isinstance(member, str) for member in members):
                self._add(path, 'a group must be a list of tool or group names')
                continue
            for member in members:
                if member not in known:
                    self._add(path, _unknown('tool or group', member, known))
        self._lint_cycles(groups)

    def _lint_cycles(self, groups):
        # report a group that transitively contains itself; without this a
        # cycle would just be silently broken at resolve time. the cycle finding
        # is the shared one (config.tools.find_cycles), so the linter and the
        # handler's expansion agree on what a cycle is
        for member in sorted(find_cycles(groups)):
            self._add(f'ai.tools.{member}', 'group has a cyclic membership')

    def _lint_profiles(self, profiles, tools):
        if not isinstance(profiles, dict):
            if profiles:
                self._add('ai.profiles', 'must be a mapping of name to profile')
            return
        agent_names = set(self._value('agents') or {})
        tool_names = set(tools) if isinstance(tools, dict) else set()
        for name, profile in profiles.items():
            path = f'ai.profiles.{name}'
            if not isinstance(profile, dict):
                self._add(path, 'a profile must be a mapping')
                continue
            self._lint_keys(path, profile, _PROFILE_KEYS)
            self._lint_options(path, profile)
            self._lint_type('provider', path, profile)
            if 'enabled' in profile and not isinstance(profile['enabled'], bool):
                self._add(f'{path}.enabled', 'must be true or false')
            # a profile binds the agent (composition); null opts out on
            # purpose, a name must exist. a profile may ```deny``` tools/groups
            # to carve out its own subset of a shared agent's tools
            agent = profile.get('agent')
            if agent is not None and agent not in agent_names:
                self._add(f'{path}.agent', _unknown('agent', agent, agent_names))
            self._lint_names(f'{path}.deny', profile.get('deny'), tool_names, 'tool or group')

    def _lint_selection(self, path, selection, tool_names):
        # a profile's ```tools``` is a selection list of item or group names from
        # ```ai.tools```, never a section of its own
        if selection is None:
            return
        if not isinstance(selection, list):
            self._add(f'{path}.tools', 'must be a list of tool or group names')
            return
        for entry in selection:
            if entry not in tool_names:
                self._add(f'{path}.tools', _unknown('tool or group', entry, tool_names))

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
