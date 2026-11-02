"""
The fixtures for all tests of the app.

pytest loads this file before any test. It keeps the temporary files of a run
in ```tmp/tests/``` of the project, runs a test marked ```isolated``` in a
pytest of its own and gives every test the fixtures ```tmp```, ```rando```,
```key``` and ```disable_stdin_capture```; a test takes a fixture by naming
it as argument.

### Example

```python
def test_report_is_written(tmp):
    # tmp.dir is an empty directory, tmp.file an empty file next to it
    path = os.path.join(tmp.dir, 'report.txt')
    with open(path, 'w') as f:
        f.write('done')
    assert os.listdir(tmp.dir) == ['report.txt']


def test_lock_of_its_own(rando):
    # a name on a shared server, a parallel run takes another one
    name = f'test_lock_{rando}'
    ...
```

### Notes

- Every test gets its own ```tmp```, ```rando``` and ```key```, nothing is
    shared
- ```disable_stdin_capture``` gives the test an empty stdin, for code that
    reads stdin while pytest captures it, e.g. ```Connection.run()``` of
    fabric
- ```tmp``` gives paths as str for code and test apps that want them,
    ```tmp_path``` of pytest gives a ```pathlib.Path```
- pytest empties ```tmp/tests/``` at the start of the next run, so a test
    cleans nothing up and its files stay for a look until then
- ```pytest --basetemp=<dir>``` puts the temporary files there instead
- A test marked ```isolated``` keeps the temporary files of its own pytest
    in ```isolated/<test>``` of them
- A fixture goes into this file only if every test may need it, the
    fixtures of a subject stay in its test files

"""

import os
import re
from io import StringIO
import pytest
from cement.utils.misc import rando as _rando
from cement.utils import fs
from tests.utils import ROOT
from tests.utils.isolation import IsolatedPytest


def pytest_configure(config):
    # the temps of the tests stay in tmp/tests of the project after the run
    if config.option.basetemp is None:
        os.makedirs(os.path.join(ROOT, "tmp"), exist_ok=True)
        config.option.basetemp = os.path.join(ROOT, "tmp", "tests")
    config.addinivalue_line("markers", "isolated: runs the test in a pytest of its own, in a fresh process")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    # a test marked isolated runs in a pytest of its own, in a fresh process
    if pyfuncitem.get_closest_marker("isolated") is None or IsolatedPytest.inside():
        return None
    name = re.sub(r"\W", "_", pyfuncitem.name)[:30]
    basetemp = os.path.join(os.path.abspath(pyfuncitem.config.option.basetemp), "isolated", name)
    IsolatedPytest(pyfuncitem.nodeid, basetemp).run()
    return True


@pytest.fixture(scope="function")
def tmp(tmp_path):
    """A cement fs.Tmp inside tmp_path, kept until the next run."""
    yield fs.Tmp(dir=str(tmp_path))


@pytest.fixture(scope="function")
def rando(request):
    """A random string of 12 characters."""
    yield _rando()[:12]


@pytest.fixture(scope="function")
def key(request):
    """A random string of 32 characters, for a name or key of full length."""
    yield _rando()


@pytest.fixture
def disable_stdin_capture(monkeypatch):
    """An empty stdin for the test, e.g. for Connection.run() of fabric."""
    monkeypatch.setattr("sys.stdin", StringIO(""))
    return monkeypatch
