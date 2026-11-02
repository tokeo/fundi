"""
Run a program of the tests in a pseudo terminal.

### Example

```python
from tests.utils.tui import Terminal
```

"""

import os
import time
import select
import subprocess
import pytest
from tests.utils import ROOT


class Terminal:
    """
    Run a program in a pseudo terminal and read its screen by pyte.

    The terminal answers the cursor position requests like a real one;
    with ```ctty``` it is the controlling terminal, so Ctrl-C sends SIGINT.
    Without the package pyte the test is skipped, without a pseudo terminal,
    as on Windows, it fails; mark such a test with a skip for that platform.

    ### Args

    - **args** (list): The program and its arguments, started in ```ROOT```
    - **rows** / **columns** (int): The size of the screen
    - **ctty** (bool): Make the terminal the controlling one of the program
    - **timeout** (float): The seconds ```wait_for``` waits at most in all

    ### Example

    ```python
    def test_prompt_greets():
        args = [sys.executable, '-c', 'print("hello", input("name> "))']
        with Terminal(args, rows=5, columns=40) as terminal:
            assert terminal.wait_for(lambda lines: lines[-1] == 'name>')
            terminal.send(b'anna\\r')
            code = terminal.wait()
        # lines are the screen rows with text, raw is every byte read
        assert terminal.lines[-1] == 'hello anna' and code == 0
    ```

    An app of the tests runs as a probe of its subject, started as module:

    ```python
    args = [sys.executable, '-m', 'tests.<folder>.<subject>_tests_<part>_probe']
    ```

    """

    def __init__(self, args, rows=24, columns=80, ctty=False, timeout=30):
        self.args, self.rows, self.columns, self.ctty, self.timeout = args, rows, columns, ctty, timeout

    def __enter__(self):
        pyte = pytest.importorskip('pyte')
        import pty
        import fcntl
        import struct
        import termios

        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', self.rows, self.columns, 0, 0))
        env = dict(os.environ, TERM='xterm-256color')
        session = dict(start_new_session=True, preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0)) if self.ctty else {}
        self.process = subprocess.Popen(self.args, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=ROOT, **session)
        os.close(slave)
        self.screen = pyte.Screen(self.columns, self.rows)
        self.stream = pyte.ByteStream(self.screen)
        self.raw = b''
        self.deadline = time.monotonic() + self.timeout
        return self

    def __exit__(self, *exc):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(10)
        os.close(self.master)

    @property
    def lines(self):
        """The lines of the screen with text."""
        return [line.rstrip() for line in self.screen.display if line.strip()]

    def _read(self, wait):
        # True on output, None without, False once the program closed the terminal
        if not select.select([self.master], [], [], wait)[0]:
            return None
        try:
            data = os.read(self.master, 65536)
        except OSError:
            return False
        self.raw += data
        self.stream.feed(data)
        for _ in range(data.count(b'\x1b[6n')):
            os.write(self.master, f'\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R'.encode())
        return True

    def wait_for(self, check):
        """Read until ```check(lines)``` holds, return the lines or None."""
        while time.monotonic() < self.deadline and self.process.poll() is None:
            lines = self.lines
            if lines and check(lines):
                return lines
            if self._read(0.05) is False:
                break
        return None

    def send(self, keys):
        """Type the keys, e.g. ```b'exit\\r'```."""
        os.write(self.master, keys)

    def wait(self, timeout=10):
        """Wait for the end of the program and its output, return its exit code."""
        code = self.process.wait(timeout)
        while self._read(0.2):
            pass
        return code
