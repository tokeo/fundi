"""
Run a model-written Python snippet and hand back the value it produced.

A snippet is a bare block of statements, so it cannot use a top-level ```return```
and has no natural place to leave its answer. This module gives it one without a
magic variable: the snippet delivers a value either by ending on an expression
(the jupyter form, where the last line is the value) or by a ```return```, and
the block is wrapped into a function so that ```return``` is legal and its value
is captured on the call. A block that delivers neither yields ```None``` -- the
contract's 'no value', which the caller reports as such.

It is kept dependency-free (the standard library ```ast``` only) so the same run
works in process and inside an isolated sandbox guest, which mounts this contract
but never the rest of tokeo.
"""

import ast


class TokeoPactSnippetError(Exception):
    """
    A snippet form this wrap cannot run.

    Raised for a top-level ```await``` (or ```async for```/```async with```): a
    plain function wrap cannot make it legal, and an async wrap is out of scope.
    The caller treats it as a tool error -- recorded as the snippet's exception
    (A) with no value delivered -- not as a machinery error.
    """


def run_snippet(code, namespace=None):
    """
    Wrap and run a snippet, returning the value it delivered or ```None```.

    The snippet is parsed (not executed) first, so its structure decides how it
    delivers: a bare last expression becomes the return value (the jupyter form),
    a ```return``` is honoured once the block is wrapped into a function, and a
    block that ends on a statement -- an assignment, a loop, an ```if``` -- runs
    for its effects and delivers ```None```. An empty block delivers ```None```
    as well.

    ### Args

    - **code** (str): The snippet source; any value-delivery form is accepted
    - **namespace** (dict | None): The globals the snippet runs in; a fresh dict
        is used when not given

    ### Returns

    - **object | None**: The value the snippet delivered, or ```None``` when it
        delivered none

    ### Raises

    - **SyntaxError**: The snippet does not parse (surfaced to the caller as is)
    - **TokeoPactSnippetError**: The snippet uses a top-level ```await```, which
        this wrap does not support

    """
    # parse, never exec: the structure is read safely first, and a parse error
    # is the snippet's own syntax error, surfaced to the caller unchanged
    tree = ast.parse(code or '')
    # a top-level await cannot run in a plain function wrap; reject it cleanly
    # rather than let the later compile fail with a cryptic message
    if _has_toplevel_async(tree):
        raise TokeoPactSnippetError('top-level await is not supported')
    # an empty body (blank source or comments only) carries no value
    if not tree.body:
        return None
    # a bare last expression is the snippet's value (jupyter form): turn it into
    # a return so the wrapping function hands that value back on the call
    last = tree.body[-1]
    if isinstance(last, ast.Expr):
        tree.body[-1] = ast.copy_location(ast.Return(value=last.value), last)
    # wrap the whole block in a function so a return -- the snippet's own or the
    # one just synthesised -- is legal; falling through returns None by itself
    wrapper = ast.FunctionDef(
        name='__tokeo_snippet__',
        args=ast.arguments(posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
        body=tree.body,
        decorator_list=[],
    )
    module = ast.Module(body=[wrapper], type_ignores=[])
    # the synthesised nodes carry no positions; fill them so compile accepts them
    ast.fix_missing_locations(module)
    ns = namespace if namespace is not None else {}
    exec(compile(module, '<python_exec>', 'exec'), ns)
    # call the wrapper: its return is the delivered value; a fall-through gives
    # None, which is the contract's 'no value delivered'
    return ns['__tokeo_snippet__']()


def _has_toplevel_async(tree):
    # an await/async-for/async-with outside an async def cannot run in a plain
    # function wrap; the same construct inside an async def is the snippet's own
    # and stays legal, so it is not flagged
    inside_async = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for child in ast.walk(node):
                inside_async.add(id(child))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            if id(node) not in inside_async:
                return True
    return False
