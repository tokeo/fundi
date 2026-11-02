"""
Tests for the interactive ```ai chat``` command of the ai extension.

The prompt is replaced by a list of lines, so the chat runs without a
terminal; the model call is replaced to see what conversation it gets.
"""

from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiResult, ChatResult
from tokeo.ext.ai import AiController, TokeoAi


class ChatTest(TokeoTest):
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


class _Lines:
    """A prompt that answers with the given lines, then ends the chat by Ctrl-D."""

    def __init__(self, lines):
        self.lines = list(lines)

    def prompt(self, message):
        """The next line, EOFError when none is left."""
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


def test_chat_drops_a_turn_that_ctrl_c_ended(monkeypatch):
    """After Ctrl-C during 'first', the next call gets only 'second' as user turn."""
    calls = []

    def chat(self, messages, **kw):
        calls.append([dict(m) for m in messages])
        if len(calls) == 1:
            raise KeyboardInterrupt
        return TokeoAiResult(answer=ChatResult(text='ok'), loopdata=None, trace=[])

    monkeypatch.setattr(AiController, '_chat_session', lambda self, history, completer: _Lines(['first', 'second']))
    monkeypatch.setattr(TokeoAi, 'chat', chat)
    cfg = init_defaults('ai')
    with ChatTest(argv=['ai', 'chat'], config_defaults=cfg) as app:
        app.run()
    assert calls[1] == [{'role': 'user', 'content': 'second'}]
