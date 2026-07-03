"""
Run a model-written Python snippet and hand back the value it produced.

A snippet is a bare block of statements, so it cannot use a top-level ```return```
and has no natural place to leave its answer. This module gives it one without a
magic variable: the snippet delivers a value by ending on an expression (the
jupyter form), by a ```return```, or by an assignment whose bound value is then
handed back; the block is wrapped into a function so that ```return``` is legal
and its value is captured on the call. A block that delivers none yields
```None``` -- the contract's 'no value', which the caller reports as such.

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
    delivers. The value comes from the last top-level statement; printed text is
    never the value (it goes to stdout). A block that delivers no value yields
    ```None``` -- the contract's 'no value'. An empty block yields ```None``` too.

    Forms that deliver a value:

    - an expression on the last line (the jupyter form), which includes calling
      a function defined earlier in the block
    - an explicit ```return```
    - a single-name assignment (```name = x```, ```name += x```, ```name: T =
      x```), delivering the bound name's value
    - a chained assignment (```a = b = x```), delivering that shared value
    - a subscript assignment (```d[k] = x```, nested or augmented), delivering
      the mutated root container (a dict or list), prior mutations included
    - a tuple or list of names (```a, b = ...```), delivering a tuple of them

    Forms that deliver nothing (```None```):

    - an attribute assignment (```o.x = ...```), since the object itself is
      generally not serializable across the sandbox boundary
    - a ```print(...)```, whose text goes to stdout, not the value
    - a plain statement: import, an uncalled def, a loop, ```if```, ```del```
    - an ambiguous assignment target: a mixed tuple (```a, d[k] = ...```) or a
      starred target (```a, *b = ...```)

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
    # the last top-level statement decides the delivered value
    last = tree.body[-1]
    if isinstance(last, ast.Expr):
        # a bare last expression is the snippet's value (jupyter form): turn it
        # into a return so the wrapping function hands that value back
        tree.body[-1] = ast.copy_location(ast.Return(value=last.value), last)
    else:
        # an assignment on the last line carries a clear value too; append a
        # return of the bound name (or mutated container / tuple) after it, so
        # the assignment runs first and the value is then handed back
        delivered = _assignment_value(last)
        if delivered is not None:
            tree.body.append(ast.copy_location(ast.Return(value=delivered), last))
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


def _assignment_value(stmt):
    # the value an assignment on the snippet's last line delivers, as an ast
    # expression to return after it -- or None when the form has no single
    # unambiguous value (an attribute write, a mixed or starred target)
    if isinstance(stmt, ast.Assign):
        # plain or chained (a = b = x): every target gets the same value, so the
        # first target that resolves to a deliverable node carries it
        for target in stmt.targets:
            value = _target_value(target)
            if value is not None:
                return value
        return None
    if isinstance(stmt, (ast.AugAssign, ast.AnnAssign)):
        return _target_value(stmt.target)
    return None


def _target_value(target):
    # turn an assignment target into the ast expression to hand back, or None
    if isinstance(target, ast.Name):
        # a single name delivers its bound value
        return ast.Name(id=target.id, ctx=ast.Load())
    if isinstance(target, (ast.Subscript, ast.Attribute)):
        # a subscript write (d[k] = x, nested) delivers the mutated root
        # container; an attribute write anywhere in the chain is excluded, as
        # the object itself is generally not serializable across the boundary
        node = target
        while isinstance(node, (ast.Subscript, ast.Attribute)):
            if isinstance(node, ast.Attribute):
                return None
            node = node.value
        return ast.Name(id=node.id, ctx=ast.Load()) if isinstance(node, ast.Name) else None
    if isinstance(target, (ast.Tuple, ast.List)):
        # a, b = ... delivers a tuple of the bound names; a non-name element
        # (a starred or nested target) makes the value ambiguous, so skip it
        names = []
        for element in target.elts:
            if not isinstance(element, ast.Name):
                return None
            names.append(ast.Name(id=element.id, ctx=ast.Load()))
        return ast.Tuple(elts=names, ctx=ast.Load())
    return None


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
