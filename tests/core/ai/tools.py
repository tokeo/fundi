"""
Importable tools for the fundi/sandbox tests.

They live in a real module (not a closure) so the subprocess sandbox worker
can import them by dotted path in a fresh interpreter, exactly as a project's
tools are imported. Each is a minimal ```TokeoAiTool```.
"""

import os
import time

from tokeo.core.ai import TokeoAiTool
from tokeo.core.ai.tool import create_tool_result


class EchoTool(TokeoAiTool):
    """Return its text argument; the simplest in/out tool."""

    class Meta:
        description = 'echo the given text'
        parameters = {
            'type': 'object',
            'properties': {'text': {'type': 'string'}},
            'required': ['text'],
        }

    def exec(self, **arguments):
        return create_tool_result(str(arguments.get('text', '')))


class CwdTool(TokeoAiTool):
    """Return the process working directory, to prove cwd took effect."""

    class Meta:
        description = 'report the current working directory'
        parameters = {'type': 'object', 'properties': {}}

    def exec(self, **arguments):
        return create_tool_result(os.getcwd())


class EnvTool(TokeoAiTool):
    """Return one environment variable, to prove env scrubbing/expansion."""

    class Meta:
        description = 'report one environment variable'
        parameters = {
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
        }

    def exec(self, **arguments):
        # an absent variable comes back as the literal "<unset>", so a test
        # can tell "set to empty" from "not set at all"
        return create_tool_result(os.environ.get(arguments['name'], '<unset>'))


class SleepTool(TokeoAiTool):
    """Sleep for the given seconds; used to trip the wall-clock timeout."""

    class Meta:
        description = 'sleep for n seconds'
        parameters = {
            'type': 'object',
            'properties': {'seconds': {'type': 'number'}},
            'required': ['seconds'],
        }

    def exec(self, **arguments):
        time.sleep(float(arguments.get('seconds', 0)))
        return create_tool_result('slept')


class RaiseTool(TokeoAiTool):
    """Raise a ValueError, to prove the sandbox catches a tool that fails."""

    class Meta:
        description = 'raise a ValueError'
        parameters = {'type': 'object', 'properties': {}}

    def exec(self, **arguments):
        raise ValueError('tool failed on purpose')


class DataTool(TokeoAiTool):
    """Return a structured value and print, to prove the three views and state."""

    class Meta:
        description = 'return a dict and write to stdout'
        parameters = {'type': 'object', 'properties': {}}

    def exec(self, **arguments):
        # print reaches the captured stdout the sandbox folds into state; the
        # dict value fills all three views (as_str/as_json/as_data) coherently
        print('side output')
        return create_tool_result(dict(answer=42, label='ok'))


class NothingTool(TokeoAiTool):
    """Return nothing at all, to prove the no-result termination (value None)."""

    class Meta:
        description = 'return nothing'
        parameters = {'type': 'object', 'properties': {}}

    def exec(self, **arguments):
        return None
