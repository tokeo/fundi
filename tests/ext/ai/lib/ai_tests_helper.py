"""
The tools the tests of the ai extension run in the sandboxes, and the test app
with the scripted model the loop and confirm tests share.

The tools live in a real module, not a closure, so the subprocess sandbox
imports them by their class path in a fresh interpreter, exactly as the tools
of a project are imported. Each one is a minimal ```TokeoAiTool```.

```LoopTest``` runs with an ai config of ```make_loop_config```, whose profile
plays the turns of a script by ```ScriptProvider```; ```make_call``` builds a
turn that calls echo, ```get_tool_contents``` reads the tool results of a run.
"""

import os
import sys
import time
import subprocess

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiTool, TokeoAiProvider, TokeoAiChatResult, TokeoAiToolCall
from tokeo.core.ai.tool import create_tool_result

SCRIPT = 'tests.ext.ai.lib.ai_tests_helper:ScriptProvider'
_ECHO = 'tests.ext.ai.lib.ai_tests_helper:EchoTool'
_CODE = 'tokeo.core.ai.tools:TokeoAiPythonTrustedExecTool'


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


class LoopTest(TokeoTest):
    """Test app with the ai extension and the extensions it builds on."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


class ScriptProvider(TokeoAiProvider):
    """
    A provider that answers from the ```script``` option of its profile.

    The script is a list of turns, each ```{'text': ..., 'calls': [(name,
    arguments), ...]}```. The turn played is the number of assistant turns in
    the messages so far, so a provider call needs no state of its own; past the
    end the last turn repeats. Every call records the messages it got in
    ```seen_messages```.
    """

    class Meta:
        config_defaults = dict(script=None)

    def __init__(self, app, *args, **kw):
        super().__init__(app, *args, **kw)
        self.seen_messages = []

    def chat(self, messages, profile, tools=None, model_params=None):
        """Play the next turn of the script, record the messages."""
        self.seen_messages.append(list(messages))
        script = profile['options']['script']
        index = min(sum(1 for m in messages if m.get('role') == 'assistant'), len(script) - 1)
        turn = script[index]
        calls = [
            TokeoAiToolCall(id=f'c{index}_{n}', name=name, arguments=arguments)
            for n, (name, arguments) in enumerate(turn.get('calls', []))
        ]
        return TokeoAiChatResult(text=turn.get('text', ''), tool_calls=calls)


def make_loop_config(script, **agent_options):
    """An ai config: echo in process, agents plain and audited, the script."""
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        license_acknowledged=False,
        defaults=dict(profile='script', agent=None),
        tools={'echo': dict(type=_ECHO), 'shout': dict(type=_ECHO), 'coding': dict(type=_CODE)},
        guards={'audit': dict(type='trace_audit'), 'redact': dict(type='regex_redact', options=dict(patterns=[r'password=\S+']))},
        # shout is configured and has a sandbox, but no agent offers it
        sandboxes={'local': dict(type='in_process', tools=['echo', 'shout', 'coding'])},
        agents={
            'plain': dict(type='fundi', options=dict(governors=[], tools=['echo', 'coding'], sandboxes=['local'], **agent_options)),
            'audited': dict(type='fundi', options=dict(tools=['echo'], governors=['audit'], sandboxes=['local'], **agent_options)),
            'redacting': dict(type='fundi', options=dict(tools=['echo'], governors=['redact'], sandboxes=['local'], **agent_options)),
        },
        profiles={'script': dict(type=SCRIPT, options=dict(script=script))},
    )
    return cfg


def make_call(text):
    """A script turn that calls echo with the text."""
    return dict(calls=[('echo', dict(text=text))])


def get_tool_contents(result):
    """The contents of the tool messages on the trace of a run, in order."""
    return [s.object['content'] for s in result.trace if isinstance(s.object, dict) and s.object.get('role') == 'tool']
