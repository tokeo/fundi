# The WASM Sandbox and the python-exec Tools

The wasm sandbox runs a tool call inside a WebAssembly guest under
[Wasmtime](https://wasmtime.dev/). It is the only sandbox in tokeo that gives
**real isolation against untrusted code** rather than mere fault-and-resource
cleanup: the guest has no network at all, sees no host file outside the paths
you explicitly mount, runs under a hard memory cap that holds on every
platform, and is interrupted by an epoch timeout. It is an opt-in extension
(```tokeo[wasm]```) and needs a user-supplied CPython-WASI build.

This document covers when to use it, the two ```python-exec``` tools, how
to install a WASI Python build into a project's ```./wasm``` folder, every option,
the file-bridge mechanics, the two trust models, the WASI standard-library
shims, and troubleshooting.


## Contents

- [When to use it](#when-to-use-it)
- [The two python-exec tools](#the-two-python-exec-tools)
- [Installing a WASI Python build into ./wasm](#installing-a-wasi-python-build-into-wasm)
- [Configuration](#configuration)
- [Options](#options)
- [How it runs: the file bridge](#how-it-runs-the-file-bridge)
- [The two trust models in depth](#the-two-trust-models-in-depth)
- [The WASI stdlib shims](#the-wasi-stdlib-shims)
- [What isolation does and does not give you](#what-isolation-does-and-does-not-give-you)
- [Troubleshooting](#troubleshooting)


## When to use it

Use the wasm sandbox for **pure computation on data you hand in**: evaluate an
expression, transform text, compute with a pure-Python library. The archetype
is "here is data, give me a result", where the tool code may be untrusted
(model-generated, foreign, experimental) but needs nothing from the outside
except its inputs.

Do **not** use it for tools whose purpose is outside access -- fetching a URL,
writing a host file, running a shell command, querying a database. Those belong
in the ```in_process``` or ```subprocess``` sandbox. The rule across the sandbox track
is: a tool defines WHAT runs, the sandbox decides WHERE; a tool that needs the
network or a path is simply not a fit for a network-less, path-less box.

The wasm sandbox is the natural home for the CodeAct pattern, where a model
writes Python at call time and the tool runs it. That is exactly the case where
"do not trust the code" turns from a nicety into a requirement, and where the
structural guarantees of wasm -- the syscalls do not exist -- are worth the
setup.


## The two python-exec tools

Both tools execute Python and return whatever the snippet assigned to a
variable named ```result``` (coerced to text for the model, kept as structured
```data``` when it is JSON-able). They differ only in their trust model, which in
turn decides HOW they reach the guest.

### ```python_untrusted_exec```

For code you must NOT trust (model-generated, foreign). It carries the marker
```wasm_direct_exec = True```, which tells the wasm sandbox to run the ```code```
argument **directly** in the guest without rebuilding the tool there. The guest
therefore imports nothing from tokeo: no framework mount is needed and the
untrusted snippet is walled off from tokeo, your app, and the host. This is the
safe default for anything a model writes.

### ```python_trusted_exec```

For code you DO trust (your own snippets, a vetted agent). It has no marker, so
the wasm sandbox rebuilds it in the guest the normal way -- which requires
tokeo, your app, and their dependencies to be mounted into the guest and put on
its ```PYTHONPATH```. Because the guest can then read that mounted code, this tool
is appropriate only for trusted input. Never point it at untrusted or
model-generated code; for that, use ```python_untrusted_exec```.

> **!!! DANGER !!!** Both tools execute Python. Configure them to run ONLY
> inside the ```wasm``` sandbox (or a hardened, throwaway ```docker``` container).
> Running either ```in_process``` or in the plain ```subprocess``` sandbox gives the
> executed code full host access -- file reads, network, process spawn -- which
> a prompt-injection can turn into data exfiltration or worse. The sandbox is
> not optional for these tools; it is the only thing that makes them safe.


## Installing a WASI Python build into ./wasm

The guest is a real CPython compiled for ```wasm32-wasi```. It is **not on PyPI**
and is **not shipped with tokeo**; you download a build once per machine and
point the sandbox options at it. The recommended source is Brett Cannon's
prebuilt WASI releases, which the Python core developers point to.

The examples below install the build into a ```./wasm``` folder at your project
root, matching the default option paths (```./wasm/python.wasm``` and
```./wasm/lib/python3.13```).

### 1. Install the runtime extra

```
pip install tokeo[wasm]
```

This pulls in ```wasmtime```, the WebAssembly runtime the sandbox drives. Nothing
else in tokeo depends on it, so projects that never use wasm stay lean.

### 2. Download and unpack a build into ./wasm

Pick a version from the releases page. The asset name contains the WASI SDK
number, which changes between releases, so copy the exact filename from
[the releases page](https://github.com/brettcannon/cpython-wasi-build/releases)
rather than assuming a number.

```
# from your project root
mkdir -p wasm
cd wasm

# example: CPython 3.13.0 built with WASI SDK 24 -- check the releases page
# for the current asset name and adjust the version/sdk number accordingly
curl -LO https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.13.0/python-3.13.0-wasi_sdk-24.zip
unzip python-3.13.0-wasi_sdk-24.zip
rm python-3.13.0-wasi_sdk-24.zip
cd ..
```

After unpacking, the ```./wasm``` folder holds the interpreter next to a ```lib```
directory with the standard library:

```
wasm/
  python.wasm            <- the interpreter (the runtime option)
  lib/
    python3.13/          <- the standard library (the stdlib option)
      ...
```

The interpreter does **not** embed the standard library, which is why ```stdlib```
is a separate read-only mount.

### 3. Keep the build out of version control

A ```python.wasm``` plus its stdlib is tens of megabytes of binary; it does not
belong in the repository. Add it to ```.gitignore```:

```
# WASI Python build for the wasm sandbox (downloaded per machine)
/wasm/
```

### 4. Point the options at it

In the sandbox options, use the relative paths under ```./wasm``` (the sandbox
resolves them against the working directory it is run from, normally the
project root):

```yaml
options:
  runtime: ./wasm/python.wasm
  stdlib: ./wasm/lib/python3.13
```

To run the wasm end-to-end tests, point the two environment variables at the
same paths so the skip-guarded tests activate:

```
export TOKEO_TEST_PYTHON_WASM="$(pwd)/wasm/python.wasm"
export TOKEO_TEST_WASI_STDLIB="$(pwd)/wasm/lib/python3.13"
```

The ```stdlib``` directory name must match the interpreter's version: a 3.13
```python.wasm``` needs ```lib/python3.13```. A mismatched stdlib will not boot.


## Configuration

The wasm sandbox and the two python-exec tools are **not registered by
default** -- the core extension registers only the framework built-ins
(```in_process```, ```subprocess```, the guards, ```fundi```, ```mock```).
Optional and security-sensitive components like these are referenced by their
full dotted class path in ```type```, which the resolver imports on demand. (A
project that uses them often can register a short name itself in a
```post_setup``` hook; the examples here use the dotted path so they work
without that.)

A minimal configuration that wires both tools, each behind its own wasm
sandbox, and exposes them through two agents:

```yaml
ai:
  tools:
    run_untrusted:
      type: tokeo.core.ai.tools.python_untrusted_exec.TokeoAiPythonUntrustedExecTool
    run_trusted:
      type: tokeo.core.ai.tools.python_trusted_exec.TokeoAiPythonTrustedExecTool

  sandboxes:
    # untrusted: total isolation, the guest sees only its stdlib
    wasm_untrusted:
      type: tokeo.core.ai.sandboxes.wasm.TokeoAiWasmSandbox
      tools:
        - run_untrusted
      options:
        runtime: ./wasm/python.wasm
        stdlib: ./wasm/lib/python3.13
        memory_mb: 256
        timeout: 10

    # trusted: the tool is rebuilt in the guest, so it needs the framework,
    # the app, and the dependencies mounted read-only and put on PYTHONPATH
    wasm_trusted:
      type: tokeo.core.ai.sandboxes.wasm.TokeoAiWasmSandbox
      tools:
        - run_trusted
      options:
        runtime: ./wasm/python.wasm
        stdlib: ./wasm/lib/python3.13
        memory_mb: 256
        timeout: 10
        mounts:
          /tokeo: /path/to/tokeo            # the tokeo source root
          /app: /path/to/your/project       # your application source root
          /deps: /path/to/site-packages     # the dependency tree (cement, ...)
        env:
          PYTHONPATH: /tokeo:/app:/deps

  agents:
    untrusted_coder:
      type: fundi
      options:
        sandboxes:
          - wasm_untrusted
    trusted_coder:
      type: fundi
      options:
        sandboxes:
          - wasm_trusted
```

When tokeo, your app, and the dependencies are all installed into the same
virtual environment, several of those mount roots collapse onto one
```site-packages``` path; mount that single path once. The mount roots are simply
the parents of the importable packages, so the same configuration works whether
tokeo is an editable sibling checkout or a normal venv install -- only the
paths differ. To find the dependency tree on a machine:

```
python -c "import cement, os; print(os.path.dirname(os.path.dirname(cement.__file__)))"
```


## Options

| Option | Required | Meaning |
| --- | --- | --- |
| ```runtime``` | yes | Path to the CPython-WASI interpreter (```python.wasm```). |
| ```stdlib``` | yes | Path to the matching WASI standard library, mounted read-only at ```/lib```. The version must match the interpreter. |
| ```mounts``` | no | A ```{guest_path: host_path}``` map of read-only mounts. This is the entire attack surface: only what you mount is visible. Empty means the guest sees no host code at all. |
| ```cwd``` | no | A host scratch directory mounted read-write at ```/work```, created on demand. |
| ```env``` | no | The guest environment: scrubbed and empty by default, only the listed keys are set, ```${NAME}``` expands against this map, then the host env, then ```''```. |
| ```timeout``` | no | Wall-clock seconds before the guest is interrupted (epoch). Enforced in-process; there is no child to kill. |
| ```memory_mb``` | no | A hard memory cap in MB via Wasmtime store limits. A runaway allocation traps the guest. Platform-independent. |
| ```shim_wasi_stdlib``` | no (default ```true```) | Mount the bundled ```multiprocessing```/```threading``` shims ahead of the stdlib so a framework that imports those names at load can be rebuilt in the guest. Only relevant on the trusted/rebuild path. |

> **Never mount a directory that holds secrets next to code.** A mounted ```.env```
> is readable inside the guest and can leave via the tool's result. Mount only
> what the guest must import, and nothing else.


## How it runs: the file bridge

There is no stdin runner here. For each call the sandbox:

1. Creates a private temporary directory and mounts it read-write at ```/io``` --
   the only writable surface the guest gets besides an optional ```/work```.
2. Writes the task (the tool's dotted path, the arguments, and its options) to
   ```/io/task.json```, and a small guest entry script to ```/io/entry.py```.
3. Mounts the stdlib read-only at ```/lib```, the requested ```mounts``` read-only at
   their guest paths, and (by default) the bundled shims at ```/lib/shims```.
4. Runs the interpreter on the entry script with a scrubbed environment, a hard
   memory cap, and an epoch deadline.
5. The guest entry either runs the ```code``` argument directly (untrusted) or
   rebuilds the tool from its dotted path and calls it (trusted), then writes
   the result to ```/io/reply.json```.
6. The host reads ```/io/reply.json``` and returns it as a ```ToolResult```.

The tool's own ```print()``` output is captured separately and cannot corrupt the
reply. Only JSON-able arguments and the result's text/data cross the bridge.
The guest's stderr is captured too, so a failed run reports why the interpreter
aborted instead of a bare wasm backtrace.


## The two trust models in depth

The difference between the tools is not in the tool code -- it is in which guest
entry the sandbox chooses, based on the tool's ```wasm_direct_exec``` marker.

**Direct path (untrusted).** The guest runs the ```code``` argument as-is. It
imports nothing from tokeo, so the box needs no framework mount. This is the
strongest isolation: the model-generated code sees only the WASI standard
library and its own snippet. It cannot import tokeo, your app, or anything you
did not mount -- and since the untrusted sandbox mounts nothing, that is
nothing at all.

**Rebuild path (trusted).** The guest imports the tool by its dotted path
(```tokeo.core.ai.tools...```) and calls it. That import chain needs tokeo, the
app, and their dependencies present on the guest's ```PYTHONPATH```, so the trusted
sandbox mounts those trees read-only. The cost is real and intentional: every
dependency the rebuilt tool pulls in must be mounted, and the executed code can
read all of it. That visibility is the price of "may use the framework", and it
is exactly why this path is for trusted input only.

A useful way to read the contrast: the untrusted path mounts nothing and asks
no questions; the trusted path mounts several trees and makes each grant of
visibility explicit in the configuration. Both behaviors are the same
deny-by-default principle seen from opposite ends.


## The WASI stdlib shims

WASI has no processes or threads, so ```multiprocessing``` and ```threading``` are
absent from the WASI standard library. Some frameworks -- cement among them --
import a couple of names from those modules at load time (for type annotations)
even when they never start a thread or process. Without help, importing such a
framework in the guest fails with ```ModuleNotFoundError```.

The sandbox ships minimal shims (```wasi_shims/multiprocessing.py``` and
```wasi_shims/threading.py```) that provide exactly those names. The lock and
thread-local types are no-ops -- which is correct in a guest that can never run
a second thread -- and any attempt to start a real ```Thread``` or ```Process``` raises
a clear error rather than silently doing nothing. The shims are mounted
read-only at ```/lib/shims``` and placed first on ```PYTHONPATH```, so they shadow the
absent modules, controlled by ```shim_wasi_stdlib``` (on by default).

This only matters on the trusted/rebuild path: the untrusted path imports no
framework and needs no shims.


## What isolation does and does not give you

The guest **cannot**:

- Open a socket -- the syscalls do not exist in its world.
- Read a host file outside the explicit mounts.
- Exceed the memory cap (a runaway allocation traps).
- Outrun the timeout.

The guest **can** still:

- Read everything you mount. wasm is only as tight as the mounts; a mounted
  secret is a readable secret.
- Return whatever you pass in. Isolation protects the host from the code, not
  the input from the code -- a secret handed in as an argument can come back in
  the result.

And wasm is a strong but not infallible sandbox. Sandbox escapes are rare but
not impossible; against a sophisticated adversary in the model, add OS-level
isolation (a disposable container) on top. For the realistic case -- keeping
foreign or model-generated code away from the host -- wasm is the right,
proportionate tool.


## Troubleshooting

**"the wasm sandbox needs a runtime ... and a stdlib path"** -- the ```runtime```
or ```stdlib``` option is unset. Point them at your ```./wasm/python.wasm``` and
```./wasm/lib/python3.x```.

**"the wasm ... path does not exist or is not a directory"** -- a mount points
at a missing host path. The message names which path and role failed. Check the
build was unpacked where the options expect, and that the trusted mounts
(tokeo / app / deps) resolve on this machine.

**A crash mentioning Py_ExitStatusException** with no clearer detail -- usually
the interpreter could not find its standard library at boot. Confirm the
```stdlib``` path exists and its version matches the interpreter.

**"No module named cement" (trusted path)** -- the dependency tree is not
mounted. Add a ```/deps``` mount for the ```site-packages``` root and put it on
```PYTHONPATH```.

**"No module named multiprocessing" or "threading" (trusted path)** -- the WASI
stdlib shims are off or not mounted. Leave ```shim_wasi_stdlib``` at its default
(```True```).

**"No module named tokeo" or your app (trusted path)** -- the tokeo or app
source root is not mounted. The trusted tool is rebuilt from
```tokeo.core.ai.tools...```, so the tokeo source must be on the guest's
```PYTHONPATH``` alongside the app.

**A "timed out after Ns" error** -- the snippet exceeded ```timeout```. Raise
it, or check the code is not waiting on something that can never happen in the
guest.
