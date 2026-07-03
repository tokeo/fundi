"""
Tests for ```run_snippet```: how a model-written snippet delivers its value.

These cover the wrap contract that replaced the old ```result =``` convention.
A snippet delivers a value either by ending on an expression (the jupyter form)
or by a ```return```, with the block wrapped into a function so the return is
legal; a block that delivers neither yields ```None```. The wrap is rejected for
a top-level ```await``` (an async wrap is out of scope), a parse error surfaces
as a ```SyntaxError```, and a runtime error propagates so the caller (the
sandbox layer) records it. Because the block becomes a function body, the
snippet's own names stay local -- the passed namespace is the run context, not
the result channel.
"""

from tokeo.pact.ai.pysnippet import run_snippet, TokeoPactSnippetError

import pytest


# -- value delivery: the snippet hands a value back -------------------------


def test_jupyter_last_expression_is_the_value():
    # a bare last expression is the value, the way a notebook cell behaves
    assert run_snippet('import math\nmath.factorial(5)') == 120


def test_top_level_return_delivers_the_value():
    # a return at top level is made legal by the wrap and hands its value back
    assert run_snippet('return sum(range(10))') == 45


def test_own_def_with_a_call_delivers():
    # the snippet may define and call its own function; the call is the last
    # expression, so its value is delivered
    assert run_snippet('def f():\n    return 7\nf()') == 7


def test_multiple_expressions_take_the_last():
    # only the last expression is the value; earlier ones run for effect
    assert run_snippet('1\n2\n3') == 3


def test_walrus_last_expression_delivers():
    # a walrus expression is still an expression, so its value is delivered
    assert run_snippet('(n := 21) * 2') == 42


def test_single_name_assignment_delivers_its_value():
    # a name bound on the last line delivers that value
    assert run_snippet('x = 5\ny = x * 2') == 10


def test_chained_assignment_delivers_the_shared_value():
    # every target of a chain gets the same value, so that value is delivered
    assert run_snippet('a = b = 21 * 2') == 42


def test_subscript_assignment_delivers_the_container():
    # writing into a dict on the last line delivers the mutated root container
    assert run_snippet("d = {}\nd['a'] = 1\nd['b'] = 2") == {'a': 1, 'b': 2}


def test_tuple_target_delivers_a_tuple_of_names():
    # a tuple of names delivers a tuple of the bound values
    assert run_snippet('a, b = 1, 2') == (1, 2)


# -- no value: the block runs but delivers nothing --------------------------


def test_attribute_assignment_delivers_none():
    # an attribute write delivers no value: the object itself is not carried
    # back, as it is generally not serializable across the sandbox boundary
    assert run_snippet('import types\no = types.SimpleNamespace()\no.x = 5') is None


def test_ambiguous_assignment_target_delivers_none():
    # a mixed tuple or a starred target has no single unambiguous value
    assert run_snippet("d = {}\na, d['k'] = 1, 2") is None
    assert run_snippet('a, *b = [1, 2, 3]') is None


def test_ending_on_a_loop_delivers_none():
    # a loop as the last statement leaves no value to hand back
    assert run_snippet('t = 0\nfor i in range(5):\n    t += i') is None


def test_def_without_a_call_delivers_none():
    # defining a function is not calling it: no value is produced
    assert run_snippet('def helper():\n    return 1') is None


def test_print_only_delivers_none():
    # a print evaluates to None, so the snippet delivers no value (the sandbox
    # captures the printed text separately)
    assert run_snippet("print('hi')") is None


def test_empty_snippet_delivers_none():
    assert run_snippet('') is None


def test_comment_only_delivers_none():
    # comments are not statements, so the body is empty: no value
    assert run_snippet('# nothing here') is None


# -- rejection and propagation ----------------------------------------------


def test_top_level_await_is_rejected():
    # a plain function wrap cannot run a top-level await; it is refused clearly
    with pytest.raises(TokeoPactSnippetError):
        run_snippet('import asyncio\nawait asyncio.sleep(0)')


def test_top_level_async_for_is_rejected():
    # async-for has the same problem as await and is refused the same way
    with pytest.raises(TokeoPactSnippetError):
        run_snippet('async for i in gen():\n    pass')


def test_await_inside_an_async_def_is_allowed():
    # an await INSIDE the snippet's own async def is legal and not flagged; the
    # def is only defined here, so the snippet delivers None
    assert run_snippet('async def f():\n    return await g()') is None


def test_syntax_error_propagates():
    # a snippet that does not parse raises, surfaced to the caller unchanged
    with pytest.raises(SyntaxError):
        run_snippet('def (')


def test_runtime_error_propagates():
    # an error while the snippet runs propagates so the sandbox layer can record
    # it on the result state, rather than being swallowed here
    with pytest.raises(ZeroDivisionError):
        run_snippet('1 / 0')


# -- namespace is the run context, not the result channel -------------------


def test_snippet_names_do_not_leak_into_the_namespace():
    # the block becomes a function body, so its assignments stay local: the
    # passed namespace is not polluted, and runs do not bleed into each other
    namespace = {}
    run_snippet('secret = 42\nsecret * 2', namespace)
    leaked = [k for k in namespace if not k.startswith('__') and k != '__tokeo_snippet__']
    assert leaked == []


def test_preset_globals_are_readable_by_the_snippet():
    # a value placed in the namespace beforehand is visible as a global to the
    # snippet -- the namespace feeds the run, it does not collect the result
    assert run_snippet('preset + 5', {'preset': 100}) == 105
