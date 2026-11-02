"""
Run a test in a pytest of its own, in a fresh process.

### Example

```python
from tests.utils.isolation import IsolatedPytest
```

"""

import os
import sys
import warnings
import subprocess
import pytest
from tests.utils import ROOT


class IsolatedPytest:
    """
    Run a test in a pytest of its own, in a fresh process.

    The fresh process inherits no threads, modules or state of the running
    pytest, e.g. for a test that forks: Python warns on ```fork()``` while
    other threads run, and a library may keep its threads for the life of
    the process. The marker ```isolated``` of ```tests.utils``` runs a test
    this way, the hook in ```conftest.py``` calls ```run()``` in place of
    the test; inside the fresh pytest the test runs as usual.

    ### Args

    - **nodeid** (str): The test, e.g.
        ```tests/core/orders/test_orders.py::test_counts_in_a_fork```
    - **basetemp** (str): The directory of the temporary files of the fresh
        pytest, emptied at its start
    - **timeout** (float): The seconds the fresh pytest may run at most

    ### Example

    ```python
    from tests.utils import isolated


    @isolated
    def test_counts_in_a_fork():
        ...
    ```

    ### Notes

    : The fixtures of the test are set up in both processes, only the fresh
        one runs the test

    : A failure shows the output of the fresh pytest, its warnings are
        passed on as one warning

    """

    #: Set in the environment of the fresh pytest, it runs the test itself
    ENVIRON = 'TOKEO_TEST_ISOLATED'

    def __init__(self, nodeid, basetemp, timeout=120):
        #: The test to run
        self.nodeid = nodeid
        #: The directory of the temporary files of the fresh pytest
        self.basetemp = basetemp
        #: The seconds the fresh pytest may run at most
        self.timeout = timeout
        #: (CompletedProcess) The finished pytest, set by ```run()```
        self.result = None

    @classmethod
    def inside(cls):
        """True inside the fresh pytest."""
        return os.environ.get(cls.ENVIRON) == '1'

    def run(self):
        """
        Run the test in a fresh pytest, fail or skip as it did there.

        ### Raises

        - **Failed**: If the test failed there
        - **Skipped**: If it ran there without passing, e.g. skipped

        """
        args = [sys.executable, '-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider', f'--basetemp={self.basetemp}', self.nodeid]
        environ = {**os.environ, self.ENVIRON: '1'}
        # pytest makes only the last directory of the basetemp
        os.makedirs(os.path.dirname(self.basetemp), exist_ok=True)
        self.result = subprocess.run(args, cwd=ROOT, env=environ, capture_output=True, text=True, timeout=self.timeout)
        output = self.result.stdout + self.result.stderr
        summary = (self.result.stdout.strip().splitlines() or [''])[-1]
        if self.result.returncode != 0:
            pytest.fail(f'isolated pytest of {self.nodeid} failed:\n{output}', pytrace=False)
        if 'warning' in summary:
            warnings.warn(f'isolated pytest of {self.nodeid} warned:\n{output}', stacklevel=2)
        if 'passed' not in summary:
            pytest.skip(f'isolated pytest of {self.nodeid}: {summary}')
