# A best practice guide to writing tests

This guide shows how the tests of this app are organized and written. A test
proves one behavior of the code. When it fails, its name and its docstring
tell what broke, without reading the code first. The rules keep the tests
small, independent and quick to read, so anyone can add a test without
studying all the others. Each rule comes with an example.

.. note::

    How the tests of the app are run, laid out and written. The rules below
    hold for everyone who writes tests here, developers and AI agents alike.
    A test checks one expectation and says which; neither tests nor their
    docs repeat what the code or the name already tells.

## Run

```sh
make test                          # all tests
make test files=tests/ext/ai       # a folder or a file
make test tests=throttle           # by name, as pytest -k
make test debug=1                  # with prints and logs on debug level
make test cov=1                    # with a coverage report
make test STRESSTESTS=1            # the stress tests too
```

The temporary files of a run stay in ```tmp/tests/``` until the next run,
```make clean``` removes them.

.. warning::

    ```python -m pytest tests``` works as well, but should not be used.
    ```make test``` prepares every run the same way: it checks the active
    venv, keeps the framework logs of cement quiet (```CEMENT_LOG=0```),
    passes ```STRESSTESTS``` on and adds the options for the selection by
    files and names, the debug output and the coverage.

## Layout

The folders of ```tests/``` follow the code under test: ```tests/core/```
tests the core library, ```tests/ext/``` the extensions and ```tests/app/```
the app itself. Below them the folders follow the package path of the code,
e.g. ```tests/core/ai/``` for ```tokeo/core/ai/```.

A subject is one module or package under test. Its tests sit in one flat
file in the folder of its package path, or, as soon as it has more than one
file, in a folder of its own named after it. When a subject folder holds
more than 6 Python files, test files, helpers and probes counted together,
its helpers and probes move into ```lib/```; the test files stay on top.
Test data lives in ```data/``` of its subject. The files in the root of
```tests/``` are the shared base.

Every folder of ```tests/``` with Python files has an empty
```__init__.py```, only the one of ```tests/``` itself has content. pytest
then imports a test file by its full path, e.g.
```tests.ext.ai.test_wasm```, so a file name only has to be unique
within its folder and repeats nothing the folder already says.

An example from the tests of fundi:

```
tests/
  conftest.py  utils/                 # shared base
  core/ai/                            # a package of the core, one file per topic
    __init__.py
    test_config.py
    test_context.py
    test_wasm.py
    ...
  ext/ai/                             # a subject with more files has a folder
    __init__.py
    ai_tests_helper.py
    test_ai.py
    test_governor.py
    test_wasm.py
    ...
```

## Where a test goes

| Code under test | Test file |
|---|---|
| a module of the core, ```tokeo/core/ai/utils.py``` | ```tests/core/ai/test_utils.py``` |
| a package of the core, ```tokeo/core/ai/``` | ```tests/core/ai/test_<topic>.py```, e.g. ```test_config.py``` |
| an extension with several test files, ```tokeo/ext/ai.py``` | ```tests/ext/ai/test_<part>.py```, e.g. ```test_wasm.py``` |

- A test file is named after what it tests, without what its folder
  already says: ```test_<module>.py``` for a module, e.g.
  ```tests/ext/test_print.py```, ```test_<topic>.py``` or
  ```test_<part>.py``` in the folder of a subject, e.g.
  ```tests/core/ai/test_config.py```, ```tests/ext/ai/test_wasm.py```
- The main file of a subject folder is named after the subject, e.g.
  ```tests/ext/ai/test_ai.py```, its parts after themselves,
  e.g. ```test_governor.py```, ```test_wasm.py```
- A new test goes into the file of its subject, below the tests of the same
  function; a new file only for a part of its own, e.g. the stress tests or
  the tests against a live server
- When a flat subject gets a second file, both move into a new folder named
  after the subject, with an ```__init__.py```, e.g.
  ```tests/ext/test_print.py``` becomes ```tests/ext/print/test_print.py```

## Shared base

The root of ```tests/``` holds what every test may use: the setup pytest
needs for all tests in ```conftest.py``` and a few general tools in
```utils/```. It comes with the app template, so every project starts
with the same base. Keeping it small lets each subject be read on its own.

| File | Holds |
|---|---|
| ```conftest.py``` | the hook that puts the temporary files into ```tmp/tests/```, the hook that runs a test marked ```isolated``` in a pytest of its own, the fixtures ```tmp```, ```rando```, ```key``` and ```disable_stdin_capture``` |
| ```utils/__init__.py``` | ```ROOT```, ```stresstest```, ```isolated```, ```wait_until```, ```wait_until_async```, ```free_port```, ```wait_process_group_ended```, ```use_disabled_stdin_capture``` |
| ```utils/process.py``` | ```SubProcess```, runs a program as a process of its own and observes it from outside |
| ```utils/tui.py``` | ```Terminal```, runs a program in a pseudo terminal |
| ```utils/isolation.py``` | ```IsolatedPytest```, runs a test in a pytest of its own, in a fresh process |

The functions and markers are imported from ```tests.utils```, each class
by the full path of its module, it is not exported by ```tests.utils```:

```python
from tests.utils import wait_until
from tests.utils.process import SubProcess
from tests.utils.tui import Terminal
from tests.utils.isolation import IsolatedPytest
```

- ```tmp``` is a cement ```fs.Tmp``` inside ```tmp_path```, with
  ```tmp.dir``` and ```tmp.file```, for a test app that takes one
- ```rando``` is a random string of 12 characters, for names that must not
  meet those of another run, e.g. a queue on a live server
- ```key``` is a random string of 32 characters, for a name or key of full
  length
- ```disable_stdin_capture``` gives the test an empty stdin, for code that
  reads stdin while pytest captures it, e.g. ```Connection.run()``` of
  fabric; ```use_disabled_stdin_capture()``` does the same for one step
- ```ROOT``` is the root of the project, the working directory of every
  program a test starts

A fixture goes into ```conftest.py``` only if every test may need it.
```utils/``` holds functions and classes, no asserts; the test asserts. A
new class gets a module of its own in ```utils/```.

.. note::

    Only code that is truly general and needed again and again goes into
    ```tests/utils/```, e.g. waiting for a condition or running a program
    in a terminal. Add to it with restraint: code of one subject stays with
    that subject, even when another subject keeps a similar copy.

## Subjects and imports

A folder, or a single flat file, is one subject and builds its own
environment.

.. note::

    **Important:** a subject keeps its test apps, helpers, functions and
    fixtures completely in its own folder. It imports the code under test
    and the shared base, nothing from other subjects, test files or tools
    outside its folder.

Every import of the tests names the full path from the project root, also
within the own subject:

```python
from tests.utils import wait_until                             # shared base
from tests.utils.tui import Terminal                           # shared base
from tests.ext.ai.ai_tests_helper import EchoTool               # own subject

# never from another subject, a conftest.py or a test file, never by a bare name
from tests.ext.ai.ai_tests_helper import EchoTool               # wrong in tests/core/ai
from tests.ext.ai.test_ai import ai_config                      # wrong
from ai_tests_helper import EchoTool                            # wrong
```

## Helpers

A helper file exists only when several test files of a subject share code;
code of one test file stays in that file.

| File | For |
|---|---|
| ```<subject>_tests_helper.py``` | the test apps and shared code |
| ```<subject>_tests_func_helpers.py``` | functions, when the helper grows too big |
| ```<subject>_tests_<part>_helper.py``` | a part of its own, e.g. ```smtpd_tests_capture_events_helper.py``` |

A helper holds test apps, functions and classes, no fixtures and no tests.
When several test files need the same fixture, the helper holds the
function that does the work, e.g. ```start_smtpd```, and each test file
keeps its own short fixture around it.

Names other files import are public, a leading underscore marks what only
the helper itself uses.

## Test apps

A test app is named after its subject and derives from the test class of
the app, ```TokeoTest``` of ```tokeo.main```. Its ```Meta``` lists the
extensions the subject needs. Differences are fixed derivations, not
functions that build apps; the class name tells what a test needs.

```python
class SchedulerTest(TokeoTest):
    """A test app with the scheduler and the given tasks."""

    class Meta:
        extensions = ['tokeo.ext.print', 'tokeo.ext.tui.input', 'tokeo.ext.tui.output', 'tokeo.ext.scheduler']

    def __init__(self, tasks=None, **kw):
        config = init_defaults('scheduler')
        config['scheduler']['tasks'] = tasks or {'time': dict(module='time', crontab='0 0 1 1 *')}
        super().__init__(config_defaults=config, **kw)


class SchedulerWithoutTuiTest(SchedulerTest):

    class Meta:
        extensions = ['tokeo.ext.print', 'tokeo.ext.tui.input', 'tokeo.ext.scheduler']
```

A base class of its own only pays when the derivations differ a lot;
otherwise the main test app is the base.

A test runs the app in a ```with``` block, which sets it up and closes it.
A command runs by ```argv``` and ```app.run()```; what it prints is read by
```capsys```, a wrong argument ends in ```SystemExit```:

```python
def test_launch_switches_removed(switch):
    """--background and --no-colors are no switches any more."""
    with SchedulerTest(argv=['scheduler', 'launch', switch]) as app:
        with pytest.raises(SystemExit):
            app.run()


def test_serve_queues_needs_a_name(serve, capsys):
    """--queues without a name exits with an error."""
    with pytest.raises(SystemExit):
        serve('--queues')
    assert '--queues' in capsys.readouterr().err
```

## Fixtures

- A fixture lives in the test file that uses it, above its first test
- It has a one-line docstring naming what it gives
- It gives one thing; a test that needs several of a kind, or differently
  configured ones, gets a factory, e.g. ```start_worker``` in the complete
  example below
- What it starts it stops after its ```yield```, also when the test fails
- Its scope is the function; ```scope='module'``` only for a costly setup
  the tests only read, e.g. certificates, or for tests sharing a state in
  order
- A subject has no ```conftest.py``` of its own
- A function of a test file that is neither a test nor a fixture starts
  with an underscore, e.g. ```_run```

```python
@pytest.fixture
def start_worker(tmp_path):
    """Starts workers on tmp_path and stops them after the test."""
    workers = []

    def start(handle):
        worker = Worker(str(tmp_path), handle)
        worker.start()
        workers.append(worker)
        return worker

    yield start
    for worker in workers:
        worker.stop()
```

## Names

- Tests are functions, no classes: ```test_<function>_<expectation>```, e.g.
  ```test_to_utc_aware_datetime_is_shifted```; for a class the class name in
  lower case or the name of its method takes the place of the function,
  e.g. ```test_worker_keeps_a_message_its_handler_failed_on```
- A file covering several modules prefixes the module, e.g.
  ```test_extensions_required_names_the_missing_ones```
- No path in the name, pytest shows it: ```tests/core/utils/test_date.py::test_...```
- The tests of one function stand together under a section comment with its
  name, with two blank lines before and after; a file that already uses
  another style of section comments keeps it

```python
# outbox_dir


def test_outbox_dir_from_config(tmp_path, monkeypatch):
    ...
```

- Tests sharing a state in order get it from a fixture with
  ```scope='module'``` and run in the order of the file, e.g. the SMTP dialog
  of ```test_process_line.py```; they are the only tests that depend on
  their order, and their module docstring says so

## Several cases of one expectation

One expectation over several cases is one test with
```@pytest.mark.parametrize```; its docstring names the expectation all
cases share. ```ids``` names the cases where their values do not read well
in the name pytest shows. Different expectations are different tests.

```python
@pytest.mark.parametrize('body', [5, {'text': None}, (5, '')], ids=['int', 'dict_none', 'tuple_int'])
def test_invalid_body(body):
    """An int or a None part as body raises TypeError."""
    with SmtpTest() as app:
        with pytest.raises(TypeError):
            app.mail._make_message(body, **app.mail._get_params())
```

## Docstrings

Every test has a docstring, pdoc shows it next to the name. It names the
expectation, with the concrete value or case where possible.

```python
def test_to_utc_aware_datetime_is_shifted():
    """14:00 at +02:00 comes back as 12:00 UTC."""
    d = datetime(2026, 6, 23, 14, 0, tzinfo=PLUS_TWO)
    assert to_utc(d) == datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
```

Not ```"""Test to_utc."""``` and not the name as a sentence. One line is the
rule; a test with a complex setup, e.g. a stress test, a probe or tests
sharing a state in order, says in a few lines what runs, what is observed
and what must hold. What all tests of a file share belongs into the module
docstring, what a probe does into the docstring of the probe. Comments
inside a test explain single steps only.

Every test file starts with a module docstring that names the code under
test and what its tests cover:

```python
"""
Tests for tokeo.core.utils.outbox: messages put into the outbox and handed
on by its worker.
"""
```

## Temporary files

A test writes only below the directory pytest gives it, never into ```/tmp```
or the project, and cleans nothing up: pytest empties ```tmp/tests/``` at the
start of the next run, the results stay for a look until then.

```python
def test_file_deleted_after_event(tmp_path):     # a directory of the test
    _run(events, {'spool': str(tmp_path) + '/'})


def test_temper_acquire_and_release(tmp):        # where a test app takes cement's fs.Tmp
    with DiskcacheTest(tmp.dir) as app:
        ...


@pytest.fixture(scope='module')                  # shared by the tests of a module
def certs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('certs')
    ...
```

A probe gets its directory from the test as an argument and creates none:

```python
args = [sys.executable, '-m', probe, tmp.dir, 'dramatiq', 'monitor', '--follow']
```

No ```tempfile```, no ```shutil.rmtree```, no ```teardown_module``` for files.

## State

A test leaves no state behind. Environment, ```sys.argv``` and module
globals change only by ```monkeypatch```, which restores them after the
test, also where the code under test changes them.

```python
monkeypatch.setattr(sys, 'argv', ['tokeo'])
monkeypatch.setenv('dramatiq_worker_timeout', '500')
monkeypatch.setattr(dramatiq_rabbitmq, 'DEAD_MESSAGE_TTL', dramatiq_rabbitmq.DEAD_MESSAGE_TTL)
```

A variable the code under test sets itself is announced first, else it
stays for every later test:

```python
monkeypatch.setenv('dramatiq_queue_prefetch', '')
monkeypatch.delenv('dramatiq_queue_prefetch')
```

A variable that may come from the shell and would change the result is
removed for the test:

```python
monkeypatch.delenv('OUTBOX_DIR', raising=False)
```

## Patch for a test or a step only

A patch reaches only as far as needed: the whole test when the test needs
the change throughout, one step when only that step may see it.

```monkeypatch``` restores its changes at the end of the test.
```monkeypatch.context()``` gives a monkeypatch of its own that restores
its changes at the end of its block, while the test goes on.

A typical case is the standard input: pytest blocks reading it, but a
program run by the code under test, e.g. by invoke, hands it on.

For the whole test:

```python
def test_tasks_task1_runs_locally(monkeypatch):
    """task1 runs uname by invoke on the local host and exits with 0."""
    # invoke hands stdin on, pytest blocks reading it
    monkeypatch.setattr('sys.stdin', io.StringIO(''))
    with AutomateTest() as app:
        task = app.automate.tasks['task1']
        ran = task['func'](app, connection, **task['kwargs'])
        ...
```

For one step only:

```python
def test_tasks_task1_runs_locally(monkeypatch):
    """task1 runs uname by invoke on the local host and exits with 0."""
    with AutomateTest() as app:
        task = app.automate.tasks['task1']
        with monkeypatch.context() as patch:
            # invoke hands stdin on, pytest blocks reading it
            patch.setattr('sys.stdin', io.StringIO(''))
            ran = task['func'](app, connection, **task['kwargs'])
        # from here on sys.stdin is the one of pytest again
        ...
```

The shared base has both for stdin: the fixture ```disable_stdin_capture```
for the whole test and ```use_disabled_stdin_capture()``` of
```tests.utils``` for one step:

```python
def test_tasks_task1_runs_locally(disable_stdin_capture):
    ...


def test_tasks_task1_runs_locally():
    with AutomateTest() as app:
        with use_disabled_stdin_capture():
            ran = task['func'](app, connection, **task['kwargs'])
```

## Waiting

A test waits for a condition, not for a fixed time.

```python
assert wait_until(lambda: len(list(tmp_path.glob('pid_*'))) == 3, 3.0)
assert await wait_until_async(lambda: srv.connections == 0)
assert wait_process_group_ended(proc.pid)        # the master and all its workers
```

```wait_until``` and ```wait_until_async``` wait 5 seconds unless a timeout
is given and return the last value of the check, so the ```assert``` shows
what failed. ```time.sleep``` stays for a test that holds a state on
purpose, e.g. a job keeping its slot while others try, and for a test
where time itself is the subject, e.g. a lock that expires or a rate
limit that refills.

## Async code

The tests use no pytest plugin for coroutines. A test of async
code is a plain function: it puts the async steps into an inner
```async def go()``` and runs it by ```asyncio.run(go())```. The asserts may
stand inside ```go```.

```python
def test_thread_safety_with_multiple_connections():
    """100 parallel deliveries never mix the ctx of their connections."""
    ev = ThreadSafetyEvents()
    ...

    async def go():
        srv, port = await start_smtpd(ev, settings=SETTINGS)
        try:
            ...
            assert await wait_until_async(lambda: srv.processings == 0)
        finally:
            await srv.stop(wait_seconds_before_close=1.0)

    asyncio.run(go())
```

## Skips

A test that needs what is not always there skips with a reason naming what
is missing, never to hide a failure.

- A missing package: ```pytest.importorskip('pyte')``` at the place that
  needs it
- A platform, a Python version or a service: a named marker at the top of
  the file, set on the tests that need it or as ```pytestmark``` on all

```python
needs_rabbitmq = pytest.mark.skipif(
    not _reachable(RABBITMQ_URL),
    reason='needs a rabbitmq at localhost:5672 (set TOKEO_TEST_RABBITMQ_URL)',
)

pytestmark = needs_rabbitmq
```

The tests of the wasm guest, ```tests/ext/ai/test_wasm.py```, need the
package ```wasmtime``` and a wasm build of Python, ```python.wasm``` with its
stdlib, as the WASM guide of the sandboxes describes. They look for it in
```wasm/``` of the project; ```TOKEO_TEST_PYTHON_WASM``` and
```TOKEO_TEST_WASI_STDLIB``` name another place; without it these tests are
skipped:

```bash
TOKEO_TEST_PYTHON_WASM=~/wasm/python.wasm TOKEO_TEST_WASI_STDLIB=~/wasm/lib/python3.13 make test
```

## Probes

A probe is a program a test starts as its own process and observes from
outside, for what only shows from there: exit codes, signals, a terminal.

- File ```<subject>_tests_<part>_probe.py```, class ```<Subject><Part>ProbeTest```
  derived from the test app of the subject
- Started as a module from the project root, never by its path:

```python
probe = 'tests.ext.tui.tui_tests_input_probe'
with Terminal([sys.executable, '-m', probe], rows=12, columns=80) as terminal:
    shown = terminal.wait_for(lambda lines: lines[-1] == 'Probe>')
    terminal.send(b'exit\r')
    terminal.wait()
```

```Terminal``` runs the program in a pseudo terminal and reads its screen by
pyte; without pyte the test is skipped. A probe without a terminal runs by
```SubProcess``` of ```tests.utils.process```, in a group of its own; a wait
that fails shows the end of its output, leaving the block ends the group:

```python
with SubProcess.probe('tests.ext.smtpd.smtpd_tests_serve_probe', config, names) as process:
    process.wait_for(lambda: banner(port))
    process.signal(signal.SIGTERM)
    assert process.wait() == 0
```

Another program, e.g. the server of a live test, runs by
```SubProcess.run(program, *args)```, a free port for it gives
```free_port()``` of ```tests.utils```.

## Stress tests

A stress test runs only with ```STRESSTESTS=1```. The marker
```stresstest``` sets it on all tests of a file or on one test:

```python
from tests.utils import stresstest

pytestmark = stresstest                          # all tests of the file


@stresstest                                      # one test
def test_worker_stress_hands_every_message_on_once(start_worker, tmp_path):
    ...
```

All its randomness comes from one seed, read from ```STRESS_SEED``` or
drawn anew, and the test prints it. pytest shows the print of a failed
test, and the same seed repeats the run:

```python
SEED = int(os.environ.get('STRESS_SEED', random.randrange(2**32)))


def test_...():
    print(f'\nSTRESS_SEED={SEED}')
    rnd = random.Random(SEED)
```

```sh
make test STRESSTESTS=1 STRESS_SEED=1234 files=tests/core/utils/test_outbox.py
```

Only ```rnd``` draws, never the functions of the module ```random```
itself, else a repeated run takes other values.

## Tests in a fresh process

All tests share one pytest process, and with it its threads, modules and
state. A test that needs a process without them is marked ```isolated```,
e.g. a test that forks: Python warns on ```fork()``` while other threads
run, and the C library of grpc keeps its threads for the life of the
process once a test built a server.

```python
from tests.utils import isolated


@isolated
def test_shared_limits_count_across_processes():
    ...
```

The hook in ```conftest.py``` runs such a test by ```IsolatedPytest``` in a
pytest of its own, started anew by ```python -m pytest <test>```; inside it
the test runs as usual.

- A failure shows the output of the fresh pytest, a skip is passed on as
  skip and its warnings as one warning
- Its temporary files stay in ```tmp/tests/isolated/<test>/```
- The fixtures of the test are set up in both processes, only the fresh one
  runs the test
- Coverage measures the fresh pytest too, by ```patch = ["subprocess"]``` of
  ```pyproject.toml```
- Each one costs a start of pytest, so mark only a test that needs it

## A complete example

The code below is made up for this guide and shows the rules together:
a module of the app and its tests in one file. All six tests pass, the
last one with ```STRESSTESTS=1```.

```python
# tokeo/core/utils/outbox.py

"""
An outbox: messages go in as files, a worker hands them on.
"""

import os
import threading
import uuid


def outbox_dir(app):
    """The directory of the outbox, OUTBOX_DIR before the config."""
    return os.environ.get('OUTBOX_DIR') or app.config.get('outbox', 'dir')


def put(directory, text):
    """Writes text as a new message and returns its file name."""
    path = os.path.join(directory, uuid.uuid4().hex)
    with open(f'{path}.tmp', 'w') as f:
        f.write(text)
    # the worker only sees complete messages
    os.replace(f'{path}.tmp', f'{path}.msg')
    return os.path.basename(path) + '.msg'


class Worker(threading.Thread):
    """Hands each message of a directory on and removes it afterwards."""

    def __init__(self, directory, handle, interval=0.02):
        super().__init__(daemon=True)
        self.directory = directory
        self.handle = handle
        self.interval = interval
        self.stopped = threading.Event()

    def run(self):
        while not self.stopped.wait(self.interval):
            for name in os.listdir(self.directory):
                if name.endswith('.msg'):
                    self._take(os.path.join(self.directory, name))

    def _take(self, path):
        with open(path) as f:
            text = f.read()
        try:
            self.handle(text)
        except Exception:
            # the message stays and is taken again in the next round
            return
        os.remove(path)

    def stop(self):
        self.stopped.set()
        self.join()
```

```python
# tests/core/utils/test_outbox.py

"""
Tests for tokeo.core.utils.outbox: messages put into the outbox and handed
on by its worker.
"""

import os
import random
import threading

import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.utils.outbox import outbox_dir, put, Worker
from tests.utils import stresstest, wait_until

# a stress run is repeated by make test STRESSTESTS=1 STRESS_SEED=<seed>
SEED = int(os.environ.get('STRESS_SEED', random.randrange(2**32)))


class OutboxTest(TokeoTest):
    """A test app with the given directory as outbox."""

    def __init__(self, directory, **kw):
        config = init_defaults('outbox')
        config['outbox']['dir'] = directory
        super().__init__(config_defaults=config, **kw)


@pytest.fixture
def start_worker(tmp_path):
    """Starts workers on tmp_path and stops them after the test."""
    workers = []

    def start(handle):
        worker = Worker(str(tmp_path), handle)
        worker.start()
        workers.append(worker)
        return worker

    yield start
    for worker in workers:
        worker.stop()


# outbox_dir


def test_outbox_dir_from_config(tmp_path, monkeypatch):
    """Without OUTBOX_DIR the directory of the config is taken."""
    monkeypatch.delenv('OUTBOX_DIR', raising=False)
    with OutboxTest(str(tmp_path)) as app:
        assert outbox_dir(app) == str(tmp_path)


def test_outbox_dir_from_env_while_set(tmp_path, monkeypatch):
    """OUTBOX_DIR wins while it is set, afterwards the config counts again."""
    monkeypatch.delenv('OUTBOX_DIR', raising=False)
    with OutboxTest(str(tmp_path)) as app:
        with monkeypatch.context() as patch:
            patch.setenv('OUTBOX_DIR', '/var/outbox')
            assert outbox_dir(app) == '/var/outbox'
        assert outbox_dir(app) == str(tmp_path)


# put


def test_put_writes_one_complete_message(tmp_path):
    """put leaves exactly one .msg file with the text and no .tmp file."""
    name = put(str(tmp_path), 'hello')
    assert [p.name for p in tmp_path.iterdir()] == [name]
    assert (tmp_path / name).read_text() == 'hello'


# Worker


def test_worker_hands_a_message_on_and_removes_it(start_worker, tmp_path):
    """A message put into the outbox is handed on once, its file is gone."""
    taken = []
    start_worker(taken.append)
    put(str(tmp_path), 'hello')
    assert wait_until(lambda: not any(tmp_path.iterdir()))
    assert taken == ['hello']


def test_worker_keeps_a_message_its_handler_failed_on(start_worker, tmp_path):
    """A message the handler fails on stays and is handed on again."""
    calls = []

    def handle(text):
        calls.append(text)
        if len(calls) == 1:
            raise ValueError('not now')

    start_worker(handle)
    put(str(tmp_path), 'hello')
    assert wait_until(lambda: not any(tmp_path.iterdir()))
    assert calls == ['hello', 'hello']


@stresstest
def test_worker_stress_hands_every_message_on_once(start_worker, tmp_path):
    """
    4 threads put 250 messages each, of random length up to 64 KiB, while
    the worker runs; every message is handed on exactly once and complete,
    no file is left.
    """
    print(f'\nSTRESS_SEED={SEED}')
    rnd = random.Random(SEED)
    batches = [[f'{t}-{i}-' + 'x' * rnd.randrange(65536) for i in range(250)] for t in range(4)]
    taken = []
    start_worker(taken.append)

    def send(batch):
        for text in batch:
            put(str(tmp_path), text)

    threads = [threading.Thread(target=send, args=(batch,)) for batch in batches]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert wait_until(lambda: len(taken) == 1000 and not any(tmp_path.iterdir()), 30.0)
    assert sorted(taken) == sorted(text for batch in batches for text in batch)
```

What it shows:

- the module docstring names the subject, each test docstring its
  expectation
- a test app named after its subject, with a fixed config
- a factory fixture that starts what the tests need and stops it after
  them
- section comments that group the tests of one function
- ```monkeypatch``` for the whole test, ```monkeypatch.context()``` for
  one step
- ```tmp_path``` as the only place to write
- ```wait_until``` instead of a fixed time
- a stress test whose seed repeats it

## Deviations

A deliberate deviation from these rules is marked right at its place:

```python
# test rules exception: numbered test names mirror the midi-smtp-server suite
```

## Checklist

- lies in the file of its subject, the file named as in "Where a test goes"
- passes alone and in any order with the others; only tests sharing a state
  in order depend on the order of their file
- writes only below its temporary directory, leaves no state
- imports nothing from other subjects or test files, every import by its
  full path
- has a docstring naming its expectation, its fixtures one naming what they
  give
- waits for conditions, not for a fixed time
- draws its randomness from a seed it prints, if it is a stress test
- ```make lint``` shows nothing and ```make fmt``` changes nothing
