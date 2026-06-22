import pytest
from contextlib import contextmanager
from io import StringIO


@contextmanager
def use_disabled_stdin_capture():
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("sys.stdin", StringIO(""))
        yield monkeypatch
