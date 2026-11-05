"""
The tools the tests of the ai extension run in the sandboxes.

They live in a real module, not a closure, so the subprocess sandbox imports
them by their class path in a fresh interpreter, exactly as the tools of a
project are imported. Each one is a minimal ```TokeoAiTool```.
"""

import os
import sys
import time
import subprocess

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


class GreetTool(TokeoAiTool):
    """Greet with a configurable prefix; proves a tool reads its own options."""

    class Meta:
        description = 'greet the given name'
        parameters = {
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
        }
        # the tool's outward surface: what a project may set under ```options```
        config_defaults = dict(
            prefix='hello',
        )

    def exec(self, **arguments):
        return create_tool_result(f"{self._config('prefix')} {arguments.get('name', '')}")


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


class SpawnTool(TokeoAiTool):
    """Start a sleep of 30 s, write its pid to pid.txt, then wait the seconds."""

    class Meta:
        description = 'start a background process, then wait'
        parameters = {
            'type': 'object',
            'properties': {'seconds': {'type': 'number'}},
            'required': ['seconds'],
        }

    def exec(self, **arguments):
        child = subprocess.Popen(['sleep', '30'])
        with open('pid.txt', 'w') as f:
            f.write(str(child.pid))
        time.sleep(float(arguments.get('seconds', 0)))
        return create_tool_result('spawned')


class ChatterTool(TokeoAiTool):
    """Print its text 20 times to stdout and stderr, with a pause between."""

    class Meta:
        description = 'print a text again and again'
        parameters = {
            'type': 'object',
            'properties': {'text': {'type': 'string'}},
            'required': ['text'],
        }

    def exec(self, **arguments):
        for _ in range(20):
            print(arguments['text'])
            print(arguments['text'], file=sys.stderr)
            time.sleep(0.005)
        return create_tool_result('done')


class LoudTool(TokeoAiTool):
    """Print the given bytes of x to stdout and stderr, then return done."""

    class Meta:
        description = 'print many bytes'
        parameters = {
            'type': 'object',
            'properties': {'size': {'type': 'integer'}},
            'required': ['size'],
        }

    def exec(self, **arguments):
        print('x' * int(arguments['size']), end='')
        print('y' * int(arguments['size']), end='', file=sys.stderr)
        return create_tool_result('done')


class BigTool(TokeoAiTool):
    """Return a text of the given bytes, to test the limit of a reply."""

    class Meta:
        description = 'return a large text'
        parameters = {
            'type': 'object',
            'properties': {'size': {'type': 'integer'}},
            'required': ['size'],
        }

    def exec(self, **arguments):
        return create_tool_result('z' * int(arguments['size']))


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
