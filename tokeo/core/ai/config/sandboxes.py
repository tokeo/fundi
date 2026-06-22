"""
Resolve which sandbox of an agent's chain contains a tool.

A sandbox chain is an agent's ordered ```sandboxes``` list; a tool runs in the
first sandbox whose ```tools``` contain it. A sandbox's ```tools``` is the
keyword ```_all``` (every tool that reaches it) or a list of tool/group names;
its ```except``` excludes members from *this* sandbox only -- not a ban, the
chain walks on for them. An exhausted chain means no sandbox lists the tool,
which is the deny-by-default.

It holds no app state and does no class loading; it works on the raw sandbox
items plus a ```resolve``` callback (the tool-group expansion) and an
```item_of``` lookup (name -> sandbox item), so the handler and the linter draw
from the same source for "does this sandbox contain this tool" and "which
sandbox runs it".
"""

from tokeo.core.utils.base import as_list


# the keyword on a sandbox's ```tools``` meaning "every tool that reaches it"
SANDBOX_TOOLS_ALL = '_all'


def sandbox_contains_tool(item, tool_name, resolve):
    """
    Does sandbox ```item```'s tools contain ```tool_name```?

    ```tools``` is ```_all``` (matches every tool) or a list of tool/group names
    (expanded through ```resolve```). ```except``` excludes members from this
    sandbox only.

    ### Args

    - **item** (dict): The sandbox item (its ```tools``` and optional
        ```except```)
    - **tool_name** (str): The tool to test
    - **resolve** (callable): ```names -> flat item list``` (the tool-group
        expansion)

    ### Returns

    - **bool**: ```True``` when the sandbox contains the tool and does not
        ```except``` it

    """
    item = item or {}
    listed = item.get('tools')
    if listed == SANDBOX_TOOLS_ALL:
        in_set = True
    else:
        # as_list so a single name (a scalar) is one name, not iterated chars
        in_set = tool_name in resolve(as_list(listed))
    if not in_set:
        return False
    excepted = resolve(as_list(item.get('except')))
    return tool_name not in excepted


def sandbox_for(tool_name, chain, item_of, resolve):
    """
    Return the name of the first sandbox in ```chain``` that runs ```tool_name```.

    Walks the ordered chain and takes the first sandbox whose tools contain the
    tool (and does not ```except``` it). An exhausted chain returns ```None```,
    which the caller turns into a deny (no sandbox listing the tool IS the
    deny-by-default).

    ### Args

    - **tool_name** (str): The tool to place
    - **chain** (list): The agent's ordered sandbox names
    - **item_of** (callable): ```name -> sandbox item``` lookup
    - **resolve** (callable): ```names -> flat item list``` (the tool-group
        expansion)

    ### Returns

    - **str | None**: The chosen sandbox name, or ```None``` when the chain is
        exhausted

    """
    for name in chain or []:
        if sandbox_contains_tool(item_of(name), tool_name, resolve):
            return name
    return None
