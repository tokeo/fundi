"""
Resolve a tool selection into a flat, deduplicated, ordered item list.

A tool selection (on an agent, a profile, a sandbox's ```tools```, or a call)
names tool items and tool groups. A group is a list value under ```ai.tools```;
an item is a leaf. This module expands groups to their member items -- groups may
contain groups -- keeping list order and dropping duplicates, with a path set
guarding against a cyclic membership.

It holds no app state and does no class loading; it works on the raw ```groups```
mapping (the list-valued entries under ```ai.tools```) plus the selected names,
so the handler and the linter draw from the same source: the handler calls
```resolve_tools``` to get the active items, the linter calls ```find_cycles```
to report a group that transitively contains itself.
"""


def resolve_tools(names, groups):
    """
    Expand a tool selection to its flat item list.

    A name that is a group (a key in ```groups```) expands to its members,
    recursively; a name that is not a group passes through as an item. Order is
    preserved and duplicates dropped. A cyclic group membership is broken (the
    repeated name is not re-entered); ```find_cycles``` is what reports it.

    ### Args

    - **names** (list): The selected tool/group names
    - **groups** (dict): The group mapping (list-valued entries under
        ```ai.tools```), ```name -> [member names]```

    ### Returns

    - **list**: The flat item names, in first-appearance order, deduplicated

    """
    resolved = []
    seen = set()

    def add(name, path):
        if name in groups:
            if name in path:
                return
            for member in groups[name]:
                add(member, path | {name})
        elif name not in seen:
            seen.add(name)
            resolved.append(name)

    for name in names or []:
        add(name, set())
    return resolved


def find_cycles(groups):
    """
    Return the group names that are part of a cyclic membership.

    A group that transitively contains itself; without reporting it, the cycle
    would just be silently broken at resolve time. The linter turns each name
    into an issue.

    ### Args

    - **groups** (dict): The group mapping, ```name -> [member names]```

    ### Returns

    - **set**: The group names that lie on a membership cycle

    """
    cyclic = set()

    def walk(name, chain):
        if name in chain:
            for member in chain[chain.index(name) :]:  # noqa E203
                cyclic.add(member)
            return
        for member in groups.get(name, []):
            if member in groups:
                walk(member, chain + [name])

    for name in groups:
        walk(name, [])
    return cyclic
