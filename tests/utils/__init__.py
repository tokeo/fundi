"""
The helpers for all tests of the app.

General tools every test may import: the root of the project, the markers
of the stress tests and of the tests in a fresh process, waiting for a
condition and an empty stdin for a step. They hold no asserts, the test
asserts on what they return. The classes live in modules of their own and
are imported by their full path: ```tests.utils.process``` runs a program
as a process of its own, ```tests.utils.tui``` runs a program in a pseudo
terminal, ```tests.utils.isolation``` runs a test in a pytest of its own.

### Example

```python
from tests.utils import ROOT_DIR, stresstest, isolated, wait_until, wait_until_async
from tests.utils import find_free_port, wait_process_group_ended
from tests.utils import use_disabled_stdin_capture
from tests.utils.process import SubProcess
from tests.utils.tui import Terminal
from tests.utils.isolation import IsolatedPytest
```

The stress tests run only with ```make test STRESSTESTS=1```, marked by
```stresstest```:

```python
pytestmark = stresstest          # all tests of the file


@stresstest                      # one test
def test_outbox_stress_hands_every_message_on_once():
    ...
```

A test that needs a process without the state of the running pytest, e.g.
one that forks, is marked by ```isolated```:

```python
@isolated
def test_counts_in_a_fork():
    ...
```

### Notes

- Only code that is truly general and needed again and again goes here,
    code of one subject stays with that subject

"""

import os
import time
import socket
import asyncio
from io import StringIO
from contextlib import contextmanager
import pytest

#: The root of the project, the working directory of every program a test
#: starts, e.g. ```subprocess.Popen([...], cwd=ROOT_DIR)```.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The marker of the stress tests, they run only with
#: ```make test STRESSTESTS=1```. Set it on all tests of a file by
#: ```pytestmark = stresstest``` or on one test by ```@stresstest```.
stresstest = pytest.mark.skipif(os.environ.get('STRESSTESTS', '0') != '1', reason='set STRESSTESTS=1 to run the stress tests')

#: The marker of the tests that run in a pytest of their own, in a fresh
#: process, by ```IsolatedPytest``` of ```tests.utils.isolation```. Set it on
#: one test by ```@isolated```.
isolated = pytest.mark.isolated


def wait_until(check, timeout=5.0, interval=0.02):
    """
    Call check until it holds or the timeout ends, return its last value.

    ### Args

    - **check** (callable): Called without arguments, holds when its value
        is true
    - **timeout** (float): The seconds to wait at most
    - **interval** (float): The seconds between two calls

    ### Returns

    - **Any**: The last value of check, true when it held in time

    ### Example

    ```python
    # the assert fails with the last value of the check
    assert wait_until(lambda: len(list(tmp_path.glob('pid_*'))) == 3, 3.0)

    # the value itself, e.g. the first line that arrived
    line = wait_until(lambda: next(iter(received), None))
    ```

    """
    end_time = time.monotonic() + timeout
    while not (result := check()) and time.monotonic() < end_time:
        time.sleep(interval)
    return result


def find_free_port():
    """
    Return a port on localhost that nobody listens on right now.

    For a server a test starts with a fixed port, e.g. one that drops port 0
    or runs as a process of its own.

    ### Returns

    - **int**: The port

    """
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_process_group_ended(pgid, timeout=5.0):
    """
    Wait until every process of the group ended, return whether they did.

    A program started with ```start_new_session=True``` leads its own group,
    its pid is the id of the group, its forked workers belong to it.

    ### Args

    - **pgid** (int): The id of the group, the pid of the started program
    - **timeout** (float): The seconds to wait at most

    ### Returns

    - **bool**: True when no process of the group is left

    ### Example

    ```python
    def test_program_ends_with_its_group():
        args = [sys.executable, '-c', 'import time; time.sleep(30)']
        process = subprocess.Popen(args, start_new_session=True, cwd=ROOT_DIR)
        try:
            process.terminate()
            assert process.wait(timeout=8) != 0
            # the program ended, and so did every process it forked
            assert wait_process_group_ended(process.pid)
        finally:
            process.kill()
    ```

    """

    def ended():
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        return False

    return wait_until(ended, timeout)


async def wait_until_async(check, timeout=5.0, interval=0.02):
    """
    Like wait_until inside a coroutine, the event loop goes on meanwhile.

    ### Args

    - **check** (callable): Called without arguments, holds when its value
        is true
    - **timeout** (float): The seconds to wait at most
    - **interval** (float): The seconds between two calls

    ### Returns

    - **Any**: The last value of check, true when it held in time

    ### Example

    ```python
    def test_server_counts_its_connections():
        async def go():
            srv, port = await start_server()
            ...
            assert await wait_until_async(lambda: srv.connection_cnt == 0)

        asyncio.run(go())
    ```

    """
    end_time = time.monotonic() + timeout
    while not (result := check()) and time.monotonic() < end_time:
        await asyncio.sleep(interval)
    return result


@contextmanager
def use_disabled_stdin_capture():
    """
    Give a step of a test an empty stdin, like the fixture disable_stdin_capture.

    ### Returns

    - **MonkeyPatch**: The monkeypatch of the step, undone at its end

    ### Example

    ```python
    def test_input_ends_on_an_empty_stdin():
        with use_disabled_stdin_capture():
            # input() reads stdin, pytest captures it
            with pytest.raises(EOFError):
                input('name> ')
    ```

    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr('sys.stdin', StringIO(''))
        yield monkeypatch
