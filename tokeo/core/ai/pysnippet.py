"""
Run a model-written Python snippet and hand back the value it produced.

A snippet is a bare block of statements, so it cannot use a top-level ```return```
and has no natural place to leave its answer. This module gives it one without a
magic variable: the snippet delivers a value by ending on an expression (the
jupyter form), by a ```return```, or by an assignment whose bound value is then
handed back. The block runs at module level like a script, so ```global``` and
functions that read its names work; a top-level ```return``` ends the run and
hands its value back. A block that delivers none yields ```None```, 'no
value', which the caller reports as such.

It uses only the standard library, so the same run works in process and in the
wasm guest, which gets a copy of this module but never the rest of tokeo.
"""

import ast


class TokeoAiSnippetError(Exception):
    """
    A snippet form the run cannot take.

    Raised for a top-level ```await``` (or ```async for```/```async with```): a
    block run at module level cannot make it legal, and an async run is out of
    scope.
    The caller treats it as a tool error (recorded as the snippet's exception
    (A) with no value delivered), not as a machinery error.
    """


def run_snippet(code, namespace=None):
    """
    Run a snippet at module level, returning the value it delivered or ```None```.

    The snippet is parsed (not executed) first, so its structure decides how it
    delivers. The value comes from the last top-level statement; printed text is
    never the value (it goes to stdout). A block that delivers no value yields
    ```None```, 'no value'. An empty block yields ```None``` too.

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
    - **namespace** (dict|None): The globals the snippet runs in; a fresh dict
        is used when not given

    ### Returns

    - **object|None**: The value the snippet delivered, or ```None``` when it
        delivered none

    ### Raises

    - **SyntaxError**: The snippet does not parse (surfaced to the caller as is)
    - **TokeoAiSnippetError**: The snippet uses a top-level ```await```, which
        this wrap does not support

    """
    # parse, never exec: the structure is read safely first, and a parse error
    # is the snippet's own syntax error, surfaced to the caller unchanged
    tree = ast.parse(code or '')
    # a top-level await cannot run at module level; reject it cleanly rather
    # than let the later compile fail with a cryptic message
    if _has_toplevel_async(tree):
        raise TokeoAiSnippetError('top-level await is not supported')
    # an empty body (blank source or comments only) carries no value
    if not tree.body:
        return None
    # a top-level return ends the run and hands its value back
    tree = _TopLevelReturn().visit(tree)
    # the last top-level statement decides the delivered value: a bare last
    # expression is the value (jupyter form), an assignment delivers what it
    # bound once the whole block ran, any other form delivers none
    last = tree.body[-1]
    if isinstance(last, ast.Expr):
        head, value = tree.body[:-1], last.value
    else:
        head, value = tree.body, _assignment_value(last)
    ns = namespace if namespace is not None else {}
    ns[_SNIPPET_RETURN_NAME] = _SnippetReturn
    try:
        # the block runs at module level in ns, like a script: global and the
        # functions it defines see its names
        exec(compile(ast.fix_missing_locations(ast.Module(body=head, type_ignores=[])), '<python_exec>', 'exec'), ns)
        if value is None:
            return None
        expression = ast.fix_missing_locations(ast.copy_location(ast.Expression(body=value), last))
        return eval(compile(expression, '<python_exec>', 'eval'), ns)
    except _SnippetReturn as returned:
        return returned.value
    finally:
        ns.pop(_SNIPPET_RETURN_NAME, None)


# the name the rewritten top-level returns raise by, reserved in the run
_SNIPPET_RETURN_NAME = '__tokeo_snippet_return__'


class _SnippetReturn(BaseException):
    # a top-level return of the snippet, carrying its value out of the run; a
    # BaseException, so an ```except Exception``` of the snippet lets it pass

    def __init__(self, value=None):
        super().__init__()
        self.value = value


class _TopLevelReturn(ast.NodeTransformer):
    # rewrite each return of the block itself into a raise of _SnippetReturn;
    # the returns of its own functions, classes and lambdas stay as they are

    def _keep(self, node):
        return node

    visit_FunctionDef = _keep
    visit_AsyncFunctionDef = _keep
    visit_ClassDef = _keep
    visit_Lambda = _keep

    def visit_Return(self, node):
        value = node.value if node.value is not None else ast.Constant(value=None)
        call = ast.Call(func=ast.Name(id=_SNIPPET_RETURN_NAME, ctx=ast.Load()), args=[value], keywords=[])
        return ast.copy_location(ast.Raise(exc=call, cause=None), node)


def _assignment_value(stmt):
    # the value an assignment on the snippet's last line delivers, as an ast
    # expression to return after it, or None when the form has no single
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
    # an await/async-for/async-with outside an async def cannot run at module
    # level; the same construct inside an async def is the snippet's own
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
