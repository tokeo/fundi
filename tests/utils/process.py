"""
Run a program of the tests as a process of its own and observe it from outside.

### Example

```python
from tests.utils.process import SubProcess
```

"""

import os
import sys
import time
import signal
import tempfile
import subprocess
import pytest
from tests.utils import ROOT_DIR, wait_process_group_ended


class SubProcess:
    """
    Run a program as a process of the test and observe it from outside.

    A probe of the tests starts by ```probe()``` as module, any other program,
    e.g. a server binary, by ```run()```. The process leads a group of its
    own and starts in ```ROOT_DIR```; its output goes to an anonymous temp file,
    so a wait that fails shows the end of the output instead of a blind
    timeout. Leaving the block kills the whole group, also after a failed
    assert.

    ### Args

    - **args** (list): The program and its arguments
    - **env** (dict, optional): More environment variables of the program
    - **timeout** (float): The seconds ```wait_for``` waits at most
    - **name** (str, optional): The name in a failure, by default the program

    ### Example

    ```python
    def test_serve_ends_on_term():
        port = find_free_port()
        probe = 'tests.ext.outbox.outbox_tests_serve_probe'
        with SubProcess.probe(probe, str(port)) as process:
            process.wait_for(lambda: _is_listening(port))
            process.signal(signal.SIGTERM)
            assert process.wait() == 0
    ```

    A program that is no probe, e.g. a server of a live test:

    ```python
    with SubProcess.run(SERVER_BIN, '--port', str(port)) as process:
        process.wait_for(lambda: _is_listening(port))
    ```

    """

    #: (file) The anonymous temp file of the output, set on enter
    log = None

    #: (Popen) The running program, set on enter
    process = None

    def __init__(self, args, env=None, timeout=30, name=None):
        #: The program and its arguments, as strings
        self.args = [str(arg) for arg in args]
        #: More environment variables of the program
        self.env = env
        #: The seconds ```wait_for``` waits at most
        self.timeout = timeout
        #: The name in a failure, by default the program
        self.name = name or os.path.basename(self.args[0])

    @classmethod
    def probe(cls, module, *args, **kwargs):
        """A probe of the tests, started as ```python -m <module> <args>```."""
        return cls([sys.executable, '-m', module, *args], name=module, **kwargs)

    @classmethod
    def run(cls, *args, **kwargs):
        """Any program with its arguments, e.g. a server binary."""
        return cls(args, **kwargs)

    def __enter__(self):
        self.log = tempfile.TemporaryFile()
        environ = {**os.environ, **(self.env or {})}
        self.process = subprocess.Popen(
            self.args, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True, env=environ, cwd=ROOT_DIR
        )
        return self

    def __exit__(self, *exc):
        # the whole group, a forked worker may outlive its master
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(10)
        wait_process_group_ended(self.process.pid)
        self.log.close()

    @property
    def pid(self):
        """The pid of the program, also the id of its group."""
        return self.process.pid

    @property
    def output(self):
        """The output of the program so far, stdout and stderr as text."""
        self.log.seek(0)
        return self.log.read().decode(errors='replace')

    def tail(self, lines=20):
        """The last lines of the output, or a note that there is none."""
        return '\n'.join(self.output.strip().splitlines()[-lines:]) or '(no output)'

    def wait_for(self, check):
        """
        Wait until ```check()``` holds, return its result.

        ### Raises

        - **Failed**: If the program ends before or the time is up, with the
            end of its output

        """
        end_time = time.monotonic() + self.timeout
        while time.monotonic() < end_time:
            result = check()
            if result:
                return result
            if self.process.poll() is not None:
                pytest.fail(f'{self.name} ended with {self.process.returncode} before it was ready:\n{self.tail()}', pytrace=False)
            time.sleep(0.05)
        pytest.fail(f'{self.name} not ready after {self.timeout} s:\n{self.tail()}', pytrace=False)

    def signal(self, sig):
        """Send the signal to the program, not to its group."""
        self.process.send_signal(sig)

    def wait(self, timeout=10):
        """
        Wait for the end of the program, return its exit code.

        ### Raises

        - **Failed**: If it still runs after timeout seconds, with the end of
            its output

        """
        try:
            return self.process.wait(timeout)
        except subprocess.TimeoutExpired:
            pytest.fail(f'{self.name} still runs after {timeout} s:\n{self.tail(30)}', pytrace=False)
