"""
Tests for ```run_snippet```: how a model-written snippet delivers its value.

These cover the wrap contract that replaced the old ```result =``` convention.
A snippet delivers a value either by ending on an expression (the jupyter form)
or by a ```return```, with the block wrapped into a function so the return is
legal; a block that delivers neither yields ```None```. The wrap is rejected for
a top-level ```await``` (an async wrap is out of scope), a parse error surfaces
as a ```SyntaxError```, and a runtime error propagates so the caller (the
sandbox layer) records it. Because the block becomes a function body, the
snippet's own names stay local; the passed namespace is the run context, not
the result channel.
"""

from tokeo.pact.ai.pysnippet import run_snippet, TokeoPactSnippetError

import pytest

# ---- value delivery: the snippet hands a value back -----------------------


def test_jupyter_last_expression_is_the_value():
    """A bare last expression is the value, like in a cell: factorial(5) is 120."""
    assert run_snippet('import math\nmath.factorial(5)') == 120


def test_top_level_return_delivers_the_value():
    """A top-level return, made legal by the wrap, delivers sum(range(10)): 45."""
    assert run_snippet('return sum(range(10))') == 45


def test_own_def_with_a_call_delivers():
    """The snippet's own f() returning 7, called last, delivers 7."""
    assert run_snippet('def f():\n    return 7\nf()') == 7


def test_multiple_expressions_take_the_last():
    """Of 1, 2, 3 only the last, 3, is the value; earlier ones run for effect."""
    assert run_snippet('1\n2\n3') == 3


def test_walrus_last_expression_delivers():
    """A walrus is still an expression: '(n := 21) * 2' delivers 42."""
    assert run_snippet('(n := 21) * 2') == 42


def test_single_name_assignment_delivers_its_value():
    """A name bound on the last line delivers its value: 'y = x * 2' is 10."""
    assert run_snippet('x = 5\ny = x * 2') == 10


def test_chained_assignment_delivers_the_shared_value():
    """A chain 'a = b = 21 * 2' delivers the value all its targets share: 42."""
    assert run_snippet('a = b = 21 * 2') == 42


def test_subscript_assignment_delivers_the_container():
    """A dict write on the last line delivers the mutated root {'a': 1, 'b': 2}."""
    assert run_snippet("d = {}\nd['a'] = 1\nd['b'] = 2") == {'a': 1, 'b': 2}


def test_tuple_target_delivers_a_tuple_of_names():
    """A tuple of names 'a, b = 1, 2' delivers the tuple of bound values (1, 2)."""
    assert run_snippet('a, b = 1, 2') == (1, 2)


# ---- no value: the block runs but delivers nothing ------------------------


def test_attribute_assignment_delivers_none():
    """'o.x = 5' delivers None: the object may not cross the sandbox boundary."""
    assert run_snippet('import types\no = types.SimpleNamespace()\no.x = 5') is None


def test_ambiguous_assignment_target_delivers_none():
    """A mixed tuple or a starred target has no single value and delivers None."""
    assert run_snippet("d = {}\na, d['k'] = 1, 2") is None
    assert run_snippet('a, *b = [1, 2, 3]') is None


def test_ending_on_a_loop_delivers_none():
    """A for loop as the last statement leaves no value to hand back: None."""
    assert run_snippet('t = 0\nfor i in range(5):\n    t += i') is None


def test_def_without_a_call_delivers_none():
    """Defining helper() without calling it produces no value: None."""
    assert run_snippet('def helper():\n    return 1') is None


def test_print_only_delivers_none():
    """print('hi') delivers None; the sandbox captures the printed text apart."""
    assert run_snippet("print('hi')") is None


def test_empty_snippet_delivers_none():
    """An empty snippet '' delivers None."""
    assert run_snippet('') is None


def test_comment_only_delivers_none():
    """Comments are no statements: a comment-only snippet delivers None."""
    assert run_snippet('# nothing here') is None


# ---- rejection and propagation --------------------------------------------


def test_top_level_await_is_rejected():
    """A top-level await cannot run in the plain wrap: TokeoPactSnippetError."""
    with pytest.raises(TokeoPactSnippetError):
        run_snippet('import asyncio\nawait asyncio.sleep(0)')


def test_top_level_async_for_is_rejected():
    """A top-level async for is refused like await: TokeoPactSnippetError."""
    with pytest.raises(TokeoPactSnippetError):
        run_snippet('async for i in gen():\n    pass')


def test_await_inside_an_async_def_is_allowed():
    """An await inside the snippet's own async def is allowed; the def is None."""
    assert run_snippet('async def f():\n    return await g()') is None


def test_syntax_error_propagates():
    """'def (' does not parse: SyntaxError reaches the caller unchanged."""
    with pytest.raises(SyntaxError):
        run_snippet('def (')


def test_runtime_error_propagates():
    """'1 / 0' raises ZeroDivisionError unswallowed, for the sandbox to record."""
    with pytest.raises(ZeroDivisionError):
        run_snippet('1 / 0')


# ---- namespace is the run context, not the result channel -----------------


def test_snippet_runs_at_module_level_in_its_namespace():
    """'secret = 42' lands in the namespace the run uses, like a script."""
    namespace = {}
    assert run_snippet('secret = 42\nsecret * 2', namespace) == 84
    assert namespace['secret'] == 42
    # the name the returns raise by is not left behind
    assert [k for k in namespace if k.startswith('__tokeo')] == []


def test_runs_without_a_namespace_share_no_names():
    """A second run without a namespace does not see 'secret' of the first."""
    run_snippet('secret = 42')
    with pytest.raises(NameError):
        run_snippet('secret')


def test_global_in_a_function_reaches_the_block():
    """A function with global total adds 5 to the total of the block: 5."""
    code = 'total = 0\ndef add(n):\n    global total\n    total += n\nadd(5)\nresult = total'
    assert run_snippet(code) == 5


def test_a_function_reads_a_name_of_the_block():
    """A function reads base = 10 of the block: f(2) gives 12."""
    assert run_snippet('base = 10\ndef f(n):\n    return base + n\nf(2)') == 12


def test_top_level_return_ends_the_run():
    """A return 10 inside a top-level if ends the run, x = 2 never runs."""
    namespace = {}
    assert run_snippet('x = 1\nif x:\n    return 10\nx = 2', namespace) == 10
    assert namespace['x'] == 1


def test_return_of_a_function_stays_its_own():
    """A return inside def f stays the function's: f() gives 3."""
    assert run_snippet('def f():\n    return 3\nf()') == 3


def test_top_level_return_passes_an_except_exception():
    """A return 7 inside try with except Exception still ends the run with 7."""
    assert run_snippet('try:\n    return 7\nexcept Exception:\n    pass\n8') == 7


def test_preset_globals_are_readable_by_the_snippet():
    """A preset 100 in the namespace is a global: 'preset + 5' gives 105."""
    # the namespace feeds the run, it does not collect the result
    assert run_snippet('preset + 5', {'preset': 100}) == 105
