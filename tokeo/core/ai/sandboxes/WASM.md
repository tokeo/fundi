# The WASM Sandbox and the python-exec Tools

The wasm sandbox runs a tool call inside a WebAssembly guest under
[Wasmtime](https://wasmtime.dev/). Next to the docker sandbox it is the one
that gives real isolation against untrusted code rather than mere crash
and resource isolation, and the only one that needs no container service:
the guest has no network at all, sees no host file outside the paths
you explicitly mount, runs under a hard memory limit that holds on every
platform, and is interrupted by an epoch timeout. It is opt-in, needs the
```wasmtime``` package (48 or later) and a user-supplied CPython-WASI build.

This document covers when to use it, the two ```python-exec``` tools, how
to install a WASI Python build into a project's ```./wasm``` directory, every option,
the file-bridge mechanics, the two trust models, what the guest receives, the
WASI standard-library shim, and troubleshooting.


## Contents

- [When to use it](#when-to-use-it)
- [The two python-exec tools](#the-two-python-exec-tools)
- [Installing a WASI Python build into ./wasm](#installing-a-wasi-python-build-into-wasm)
- [Configuration](#configuration)
- [Options](#options)
- [How it runs: the file bridge](#how-it-runs-over-the-file-bridge)
- [The two trust models in depth](#the-two-trust-models-in-depth)
- [What the guest receives](#what-the-guest-receives)
- [The WASI stdlib shim](#the-wasi-stdlib-shim)
- [What isolation does and does not give you](#what-isolation-does-and-does-not-give-you)
- [Troubleshooting](#troubleshooting)


## When to use it

Use the wasm sandbox for pure computation on data you hand in: evaluate an
expression, transform text, compute with a pure-Python library. The archetype
is "here is data, give me a result", where the tool code may be untrusted
(model-generated, foreign, experimental) but needs nothing from the outside
except its inputs.

Do not use it for tools whose purpose is outside access: fetching a URL, writing a
host file, running a shell command, querying a database. Those belong in the
```in_process```, ```subprocess``` or ```docker``` sandbox. The rule across the sandbox track is:
a tool defines what runs, the sandbox decides where; a tool that needs the network
or a path is simply not a fit for a network-less, path-less box.

The wasm sandbox is the natural home for the CodeAct pattern, where a model
writes Python at call time and the tool runs it. That is exactly the case where
"do not trust the code" turns from a nicety into a requirement, and where the
structural guarantees of wasm (the syscalls do not exist) are worth the
setup.


## The two python-exec tools

Both tools execute Python and return the value the snippet delivers, either as
its last line (an expression, the way a notebook cell yields a value) or with a
```return```. The tool hands that raw value back; the sandbox layer prepares it
into the model-facing string and the json view the framework builds. The tools
differ only in their trust model, which in turn decides how they reach the
guest.

### ```python_untrusted_exec```

For code you must not trust (model-generated, foreign). It carries the marker
```is_wasm_snippet = True```, which tells the wasm sandbox to run the ```code```
argument directly in the guest without rebuilding the tool there. The guest
therefore rebuilds no tool from tokeo: no framework mount is needed and the
untrusted snippet is walled off from the framework, your app, and the host. It
gets only copies of a few stdlib-only helper modules (see what the guest
receives below), which change none of that. This is the safe default for
anything a model writes.

### ```python_trusted_exec```

For code you do trust (your own snippets, a vetted agent). It has no marker, so
the wasm sandbox rebuilds it in the guest the normal way, which requires
tokeo, your app, and their dependencies to be mounted into the guest and put on
its ```PYTHONPATH```. Because the guest can then read that mounted code, this tool
is appropriate only for trusted input. Never point it at untrusted or
model-generated code; for that, use ```python_untrusted_exec```.

.. danger::

    Both tools execute Python. Configure them to run only inside the ```wasm```
    sandbox (or a hardened, throwaway ```docker``` container). Running either
    ```in_process``` or in the plain ```subprocess``` sandbox gives the executed
    code full host access (file reads, network, process spawn), which a
    prompt-injection can turn into data exfiltration or worse. The sandbox is not
    optional for these tools; it is the only thing that makes them safe.


## Installing a WASI Python build into ./wasm

The guest is a real CPython compiled for ```wasm32-wasi```. It is not on PyPI
and is not shipped with tokeo; you download a build once per machine and
point the sandbox options at it. The recommended source is Brett Cannon's
prebuilt WASI releases, which the Python core developers point to.

The examples below install the build into a ```./wasm``` directory at your project
root, the paths the examples and the ramani template use
(```./wasm/python.wasm``` and ```./wasm/lib/python3.13```). The options
```runtime_path``` and ```stdlib_dir``` have no default; a call without them
fails.

### 1. Install the runtime

```
pip install 'tokeo-fundi[wasm]'
```

The extra ```wasm``` installs ```wasmtime```, the WebAssembly runtime the
sandbox drives. fundi does not install it by default, so projects that never use
wasm stay lean. Version 48 or later is needed, older versions mount directories
by another api.

### 2. Download and unpack a build into ./wasm

Pick a version from the releases page. The asset name contains the WASI SDK
number, which changes between releases, so copy the exact filename from
[the releases page](https://github.com/brettcannon/cpython-wasi-build/releases)
rather than assuming a number.

```
# from your project root
mkdir -p wasm
cd wasm

# example: CPython 3.13.0 built with WASI SDK 24, check the releases page
# for the current asset name and adjust the version/sdk number accordingly
curl -LO https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.13.0/python-3.13.0-wasi_sdk-24.zip
unzip python-3.13.0-wasi_sdk-24.zip
rm python-3.13.0-wasi_sdk-24.zip
cd ..
```

After unpacking, the ```./wasm``` directory holds the interpreter next to a ```lib```
directory with the standard library:

```
wasm/
  python.wasm            <- the interpreter (the runtime_path option)
  lib/
    python3.13/          <- the standard library (the stdlib_dir option)
      ...
```

The interpreter does not embed the standard library, which is why
```stdlib_dir``` is a separate read-only mount.

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
  runtime_path: ./wasm/python.wasm
  stdlib_dir: ./wasm/lib/python3.13
```

To run the wasm end-to-end tests, point the two environment variables at the
same paths so the skip-guarded tests activate:

```
export TOKEO_TEST_PYTHON_WASM="$(pwd)/wasm/python.wasm"
export TOKEO_TEST_WASI_STDLIB="$(pwd)/wasm/lib/python3.13"
```

The directory name of ```stdlib_dir``` must match the interpreter's version: a 3.13
```python.wasm``` needs ```lib/python3.13```. A mismatched stdlib will not boot.


## Configuration

The wasm sandbox and the two python-exec tools are not registered by
default; the core extension registers only the framework built-ins
(```in_process```, ```subprocess```, ```docker```, the guards, ```fundi```,
```mock```, ```oai_compat```).
Optional and security-sensitive components like these are referenced by their
full class path in ```type``` (```module:Class```), which the resolver imports
on demand. (A project that uses them often can register an alias itself in a
```post_setup``` hook; the examples here use the class path so they work
without that.)

A minimal configuration that wires both tools, each behind its own wasm
sandbox, and exposes them through two agents:

```yaml
ai:
  tools:
    run_untrusted:
      type: tokeo.core.ai.tools.python_untrusted_exec:TokeoAiPythonUntrustedExecTool
    run_trusted:
      type: tokeo.core.ai.tools.python_trusted_exec:TokeoAiPythonTrustedExecTool

  sandboxes:
    # untrusted: strong isolation, the guest sees its stdlib and /io only:
    # no framework, no app, no host paths
    wasm_untrusted:
      type: tokeo.core.ai.sandboxes.wasm:TokeoAiWasmSandbox
      tools:
        - run_untrusted
      options:
        runtime_path: ./wasm/python.wasm
        stdlib_dir: ./wasm/lib/python3.13
        max_memory_mb: 256
        timeout: 10

    # trusted: the tool is rebuilt in the guest, so it needs the framework,
    # the app, and the dependencies mounted read-only and put on PYTHONPATH
    wasm_trusted:
      type: tokeo.core.ai.sandboxes.wasm:TokeoAiWasmSandbox
      tools:
        - run_trusted
      options:
        runtime_path: ./wasm/python.wasm
        stdlib_dir: ./wasm/lib/python3.13
        max_memory_mb: 256
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
        tools: [run_untrusted]
        governors: []
        sandboxes: [wasm_untrusted]
    trusted_coder:
      type: fundi
      options:
        tools: [run_trusted]
        governors: []
        sandboxes: [wasm_trusted]
```

When tokeo, your app, and the dependencies are all installed into the same
virtual environment, several of those mount roots collapse onto one
```site-packages``` path; mount that single path once. The mount roots are simply
the parents of the importable packages, so the same configuration works whether
tokeo is an editable sibling checkout or a normal venv install; only the
paths differ. To find the dependency tree on a machine:

```
python -c "import cement, os; print(os.path.dirname(os.path.dirname(cement.__file__)))"
```


## Options

The options of the wasm sandbox, with their defaults and allowed values, are
specified in one place: section 5.5 of ```CONFIG.md``` (```tokeo/core/ai/config```),
next to the other sandboxes and a table of what each sandbox can enforce. What
matters beyond the table:

- ```mounts``` are the entire attack surface: only what you mount is visible.
  Empty means the guest sees no host code at all.
- ```max_disk_mb``` counts what the guest adds to ```/io``` and ```/work``` and what it
  writes to stdout and stderr. What is in ```/work``` before the run does not
  count.
- ```watch_interval_seconds```: each check walks ```/io```, ```/work``` and the
  captured output, so a large ```/work``` costs more per check; a longer
  interval lets the guest write more past the limit before it is stopped. A check after the
  run catches what is left.
- ```max_output_mb``` cuts stdout and stderr at that size (with a mark) and makes a
  larger value an error.
- ```cache_dir```: the first run compiles ```python.wasm``` (about 2 s) and stores
  it there, later runs of any process load it in milliseconds. A file in it is
  native code the host runs, so only the operator may write the directory.
- ```wasi_shims``` puts the bundled ```multiprocessing``` shim ahead of the
  stdlib, so a framework that imports it at load can be rebuilt in the guest.
  Only on the trusted path; the untrusted guest never gets it.

.. warning::

    Never mount a directory that holds secrets next to code. A mounted ```.env```
    is readable inside the guest and can leave via the tool's result. Mount only
    what the guest must import, and nothing else.


## How it runs over the file bridge

There is no stdin runner here. For each call the sandbox:

1. Creates a private temporary directory and mounts it read-write at ```/io```,
   the only writable surface the guest gets besides an optional ```/work```.
2. Writes the task (the arguments; on the trusted path also the tool's module,
   class, and options) to ```/io/task.json```, a small guest entry script to
   ```/io/entry.py```, and copies of the guest modules to ```/io/_guest``` (see
   what the guest receives below).
3. Mounts the stdlib read-only at ```/lib``` and the requested ```mounts```
   read-only at their guest paths.
4. Runs the interpreter on the entry script with a scrubbed environment, a hard
   memory limit, and an epoch deadline. The guest's stdout and stderr go to files
   the host keeps outside ```/io```, out of the guest's reach; they count for
   ```max_disk_mb``` and are cut at ```max_output_mb```.
5. The guest entry either runs the ```code``` argument directly (untrusted,
   ```_GUEST_ENTRY_EXEC_PYSNIPPET```) or rebuilds the tool and calls it
   (trusted, ```_GUEST_ENTRY_EXEC_TOOL```); the two are detailed under the trust
   models below. Either way it delivers only the value, as data in
   ```/io/value.json```: its model-facing string, its json, and whether the json
   is incomplete. No file means no value.
6. The host builds the ```ToolResult```: the value from ```/io/value.json``` (read
   with ```json.loads``` up to ```max_output_mb```), the states from what it
   observed itself. A clean exit leaves ```error``` empty. A tool or snippet
   that raises exits with code 1: the result has no value and the last stderr
   line as ```error``` (a tool error, A). A failure of the entry's own
   machinery (the task, the guest modules, the tool import) exits with code 3
   and is raised as a ```TokeoAiError``` (a machinery error, B), as are a crash,
   a timeout, and a broken value.

Only JSON-able arguments and the value as data cross the bridge; the host
rebuilds ```as_data``` from ```as_json```, so a value's structured form survives
without the guest having to send a possibly non-JSON-able object. The host
captures ```stdout``` and ```stderr``` and reads the exit code itself; on a failed
exit the last stderr line becomes ```error```. The guest still writes the
text of those streams and reports ```is_incomplete```, so these states are a hint
the host does not trust; it decides only on the exit code, a trap, the
timeout and the limits it observed.

The whole path, host and guest across the file bridge, at a glance: the
host writes the task and picks the entry, the guest runs it and writes the
value, the host builds the ```ToolResult``` (or raises on a machinery error):

<svg width="100%" viewBox="0 0 680 600" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" role="img" aria-label="Full host and guest file-bridge flow of the wasm sandbox">
  <defs>
    <marker id="ah3" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <rect x="30" y="24" width="300" height="552" rx="14" fill="none" stroke="#888780" stroke-width="0.5" stroke-dasharray="4 4"/>
  <text x="44" y="44" font-size="12" fill="#5F5E5A">host</text>
  <rect x="350" y="24" width="300" height="552" rx="14" fill="none" stroke="#888780" stroke-width="0.5" stroke-dasharray="4 4"/>
  <text x="364" y="44" font-size="12" fill="#5F5E5A">guest (wasm)</text>

  <rect x="60" y="58" width="240" height="50" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="180" y="78" text-anchor="middle" font-size="14" font-weight="500" fill="#042C53">exec(tool, arguments)</text>
  <text x="180" y="96" text-anchor="middle" font-size="12" fill="#0C447C">build task dict</text>

  <line x1="180" y1="108" x2="180" y2="126" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="128" width="240" height="56" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="180" y="151" text-anchor="middle" font-size="14" font-weight="500" fill="#042C53">write task.json + entry.py</text>
  <text x="180" y="169" text-anchor="middle" font-size="12" fill="#0C447C">choose pysnippet vs tool</text>

  <line x1="180" y1="184" x2="180" y2="202" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="204" width="240" height="74" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="180" y="226" text-anchor="middle" font-size="14" font-weight="500" fill="#042C53">_run_guest: mount + start</text>
  <text x="180" y="246" text-anchor="middle" font-size="12" fill="#0C447C">/lib stdlib, /io scratch</text>
  <text x="180" y="264" text-anchor="middle" font-size="12" fill="#0C447C">capture stdout, stderr</text>

  <path d="M300 241 L378 241" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="380" y="204" width="240" height="74" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="500" y="226" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">entry.py runs</text>
  <text x="500" y="246" text-anchor="middle" font-size="12" fill="#3C3489">pysnippet: run_snippet</text>
  <text x="500" y="264" text-anchor="middle" font-size="12" fill="#3C3489">tool: load + tool.exec</text>

  <line x1="500" y1="278" x2="500" y2="304" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="380" y="306" width="240" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="500" y="329" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">encode the value</text>
  <text x="500" y="347" text-anchor="middle" font-size="12" fill="#3C3489">with _guest.json</text>

  <line x1="500" y1="362" x2="500" y2="386" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="380" y="388" width="240" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="500" y="413" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">write /io/value.json</text>

  <path d="M378 410 L302 410" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="388" width="240" height="44" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="180" y="413" text-anchor="middle" font-size="14" font-weight="500" fill="#042C53">read exit code, value</text>

  <line x1="150" y1="432" x2="150" y2="456" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="458" width="240" height="48" rx="8" fill="#FCEBEB" stroke="#A32D2D" stroke-width="0.5"/>
  <text x="180" y="479" text-anchor="middle" font-size="14" font-weight="500" fill="#501313">error to TokeoAiError</text>
  <text x="180" y="496" text-anchor="middle" font-size="12" fill="#791F1F">exit 3, crash, timeout (B)</text>

  <line x1="210" y1="432" x2="210" y2="524" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="526" width="240" height="48" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="180" y="547" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">build the ToolResult</text>
  <text x="180" y="564" text-anchor="middle" font-size="12" fill="#0F6E56">guest value, host exit and captures</text>
</svg>


## The two trust models in depth

The difference between the tools is not in the tool code; it is in which guest
entry the sandbox chooses, based on the tool's ```is_wasm_snippet``` marker.

Pysnippet path (untrusted). The guest runs the ```code``` argument as-is. It
rebuilds no tool from tokeo, so the box needs no framework mount, no app, and no
dependency tree. The model-generated code sees only the WASI standard library,
its own snippet, and the copied guest modules (see below), nothing of the
framework, your app, or any host path you did not mount. That is the strongest
isolation the sandbox offers.

The exec-pysnippet guest entry (```_GUEST_ENTRY_EXEC_PYSNIPPET```) in detail: it
imports ```run_snippet``` and the json encoder from ```_guest```, reads the
```code``` argument, runs it directly, and writes the value; no tool is loaded,
so no ```importlib``` and no framework. A snippet that raises, fails to parse,
or uses an unsupported form exits with code 1, a tool error (A); a failure
before the snippet runs exits with code 3, a machinery error (B):

<svg width="100%" viewBox="0 0 680 520" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" role="img" aria-label="Flow of the exec-pysnippet guest entry">
  <defs>
    <marker id="ah2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <rect x="240" y="30" width="200" height="50" rx="8" fill="#F1EFE8" stroke="#888780" stroke-width="0.5"/>
  <text x="340" y="50" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">import _guest</text>
  <text x="340" y="68" text-anchor="middle" font-size="12" fill="#5F5E5A">json + run_snippet (no importlib)</text>

  <line x1="340" y1="80" x2="340" y2="98" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="100" width="200" height="56" rx="8" fill="#F1EFE8" stroke="#888780" stroke-width="0.5"/>
  <text x="340" y="123" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">read task, get code</text>
  <text x="340" y="141" text-anchor="middle" font-size="12" fill="#5F5E5A">arguments['code']</text>

  <line x1="340" y1="156" x2="340" y2="174" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="176" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="199" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">run_snippet(code, ns)</text>
  <text x="340" y="217" text-anchor="middle" font-size="12" fill="#3C3489">stdout to the host capture</text>

  <rect x="40" y="180" width="170" height="48" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="125" y="201" text-anchor="middle" font-size="14" font-weight="500" fill="#412402">snippet fails = A</text>
  <text x="125" y="218" text-anchor="middle" font-size="12" fill="#633806">exit 1, no value</text>
  <line x1="240" y1="204" x2="212" y2="204" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>

  <line x1="340" y1="232" x2="340" y2="250" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="252" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="275" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">None or encode value</text>
  <text x="340" y="293" text-anchor="middle" font-size="12" fill="#3C3489">as_str, as_json, is_incomplete</text>

  <path d="M125 228 L125 280 L238 280" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>

  <line x1="340" y1="308" x2="340" y2="326" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="328" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="351" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">no value: no file</text>
  <text x="340" y="369" text-anchor="middle" font-size="12" fill="#3C3489">the host reads the states</text>

  <line x1="340" y1="384" x2="340" y2="450" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="452" width="200" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="477" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">write /io/value.json</text>

  <rect x="470" y="176" width="170" height="64" rx="8" fill="#FCEBEB" stroke="#A32D2D" stroke-width="0.5"/>
  <text x="555" y="197" text-anchor="middle" font-size="14" font-weight="500" fill="#501313">setup fails = B</text>
  <text x="555" y="215" text-anchor="middle" font-size="12" fill="#791F1F">task or _guest import</text>
  <text x="555" y="231" text-anchor="middle" font-size="12" fill="#791F1F">exit 3</text>
  <path d="M440 128 L555 128 L555 174" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <path d="M555 240 L555 474 L442 474" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
</svg>

Tool path (trusted). The guest imports the tool by its module and class
(```tokeo.core.ai.tools...```) and calls it. That import chain needs tokeo, the
app, and their dependencies present on the guest's ```PYTHONPATH```, so the trusted
sandbox mounts those trees read-only. The cost is real and intentional: every
dependency the rebuilt tool pulls in must be mounted, and the executed code can
read all of it. That visibility is the price of "may use the framework", and it
is exactly why this path is for trusted input only.

The exec-tool guest entry (```_GUEST_ENTRY_EXEC_TOOL```) in detail: it reads
the task, rebuilds the tool with ```app=None``` and calls its ```exec```. A
returned ```ToolResult``` hands over its value and ```is_incomplete```; its other
states are not taken, the host captures them. A tool that raises exits with code 1, a tool error (A); a
failure before the call (the task, the import, the setup) exits with code 3, a
machinery error (B):

<svg width="100%" viewBox="0 0 680 560" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" role="img" aria-label="Flow of the exec-tool guest entry">
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <rect x="240" y="30" width="200" height="50" rx="8" fill="#F1EFE8" stroke="#888780" stroke-width="0.5"/>
  <text x="340" y="50" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">import _guest</text>
  <text x="340" y="68" text-anchor="middle" font-size="12" fill="#5F5E5A">json (+ mounted tokeo)</text>

  <line x1="340" y1="80" x2="340" y2="98" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="100" width="200" height="44" rx="8" fill="#F1EFE8" stroke="#888780" stroke-width="0.5"/>
  <text x="340" y="125" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">read /io/task.json</text>

  <line x1="340" y1="144" x2="340" y2="162" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="164" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="187" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">_load: importlib</text>
  <text x="340" y="205" text-anchor="middle" font-size="12" fill="#3C3489">rebuild tool, app=None</text>

  <line x1="340" y1="220" x2="340" y2="238" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="240" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="263" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">tool.exec</text>
  <text x="340" y="281" text-anchor="middle" font-size="12" fill="#3C3489">stdout to the host capture</text>

  <rect x="40" y="244" width="170" height="48" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="125" y="265" text-anchor="middle" font-size="14" font-weight="500" fill="#412402">tool raises = A</text>
  <text x="125" y="282" text-anchor="middle" font-size="12" fill="#633806">exit 1, no value</text>
  <line x1="240" y1="268" x2="212" y2="268" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>

  <line x1="340" y1="296" x2="340" y2="314" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="316" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="339" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">take the value</text>
  <text x="340" y="357" text-anchor="middle" font-size="12" fill="#3C3489">None / encode / ToolResult</text>

  <path d="M125 292 L125 344 L238 344" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>

  <line x1="340" y1="372" x2="340" y2="390" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="392" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="415" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">no value: no file</text>
  <text x="340" y="433" text-anchor="middle" font-size="12" fill="#3C3489">the host reads the states</text>

  <line x1="340" y1="448" x2="340" y2="488" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="490" width="200" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="515" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">write /io/value.json</text>

  <rect x="470" y="240" width="170" height="64" rx="8" fill="#FCEBEB" stroke="#A32D2D" stroke-width="0.5"/>
  <text x="555" y="261" text-anchor="middle" font-size="14" font-weight="500" fill="#501313">setup fails = B</text>
  <text x="555" y="279" text-anchor="middle" font-size="12" fill="#791F1F">task, import, setup</text>
  <text x="555" y="295" text-anchor="middle" font-size="12" fill="#791F1F">exit 3</text>
  <path d="M440 192 L555 192 L555 238" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <path d="M555 304 L555 512 L442 512" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
</svg>

A useful way to read the contrast: the untrusted path mounts nothing beyond the
stdlib and asks no further questions; the trusted path
mounts several trees and makes each grant of visibility explicit in the
configuration. Both behaviors are the same deny-by-default principle seen from
opposite ends.


## What the guest receives

Besides the stdlib at ```/lib```, the guest gets nothing of tokeo. For each run
the sandbox copies the few modules the guest entries need into ```/io/_guest```,
a package next to the entry script:

| Module | Purpose in the guest |
| --- | --- |
| ```tokeo.core.utils.json``` | encodes the value like the host |
| ```tokeo.core.utils.date```, ```tokeo.core.utils.date_compat``` | used by the json encoder |
| ```tokeo.core.ai.pysnippet``` | runs the snippet and finds its value |

The list is ```WASM_GUEST_MODULE_COPIES``` in ```wasm.py```. The source of each module comes
from the import system of the host, so the guest always runs the code the host
runs. A missing source ends the run with an error. The rules:

- No package or repository is mounted for this; the copies live and die with the
  run's ```/io```, there is no cache.
- Every module of the list imports only the standard library or another module
  of the list; a test checks that by the syntax tree.
- The guest writes only ```/io``` and ```/work```; everything else is read-only.
- The guest hands back only data: the value in ```/io/value.json```, read with
  ```json.loads``` up to ```max_output_mb```, with ```is_incomplete```. The
  exit code, a trap and the timeout are observed by the host; the text of
  stdout, stderr and the error is the guest's and only a hint.
- ```PYTHONDONTWRITEBYTECODE=1```: the guest writes no bytecode next to the
  copies.

The guest encodes a value with the same json module as the host, and a test runs
values of several kinds through both to show the encodings match.

.. warning::

    The ```cache_dir``` holds native code the host runs. Only the
    operator may write to it, like to the virtual environment; whoever can
    write there can run code in the app. A cache file is named by the hash of
    ```python.wasm```; one compiled by another wasmtime is compiled anew.


## The WASI stdlib shim

WASI has no processes, so ```multiprocessing``` is absent from the WASI
standard library. Some frameworks (cement among them) import a name from it at
load time even when they never start a process. Without help, rebuilding such a
framework in the guest fails with ```ModuleNotFoundError```.

The sandbox ships a minimal shim (```wasi_shims/multiprocessing.py```) that
provides that name; any attempt to start a real ```Process``` raises a clear
error. On the trusted path, and only there, it is copied to ```/io/_shims``` and
placed first on ```PYTHONPATH```, controlled by ```wasi_shims``` (on by
default). The untrusted guest imports no framework and never gets it.
```threading``` needs no shim: the WASI builds bring it themselves.


## What isolation does and does not give you

The guest cannot:

- Open a socket; the syscalls do not exist in its world.
- Read a host file outside the explicit mounts.
- Exceed the memory limit (a runaway allocation traps).
- Outrun the timeout, or write more than ```max_disk_mb``` or ```max_files``` into
  ```/io``` and ```/work```.

A timer stops the guest when its timeout is up; a watcher thread checks the
bytes and the files every ```watch_interval_seconds``` (default 1 s, at least
0.1 s). Both stop the guest through Wasmtime's epoch interruption, which is always on.
A guest that broke a limit and ended before the next check fails all the same:
a last check runs after the guest has ended. Epoch interruption makes CPU-bound
guest code about 30 % slower; it is the only way to stop a running guest from
outside.

The guest can still:

- Read everything you mount. wasm is only as tight as the mounts; a mounted
  secret is a readable secret.
- Return whatever you pass in. Isolation protects the host from the code, not
  the input from the code; a secret handed in as an argument can come back in
  the result.

And wasm is a strong but not infallible sandbox. Sandbox escapes are rare but
not impossible; against a sophisticated adversary in the model, add OS-level
isolation (a disposable container) on top. For the realistic case (keeping
foreign or model-generated code away from the host), wasm is the right,
proportionate tool.


## Troubleshooting

"the wasm sandbox needs runtime_path ... and stdlib_dir": the
```runtime_path``` or ```stdlib_dir``` option is unset. Point them at your
```./wasm/python.wasm``` and ```./wasm/lib/python3.x```.

"the wasm ... path does not exist or is not a directory": a mount points
at a missing host path. The message names which path and role failed. Check the
build was unpacked where the options expect, and that the trusted mounts
(tokeo / app / deps) resolve on this machine.

"the wasm runtime path does not exist or is not a file": ```runtime_path```
points at a missing ```python.wasm```. The stdlib was found, the runtime was
not built or not copied.

A crash mentioning Py_ExitStatusException with no clearer detail: usually
the interpreter could not find its standard library at boot. Confirm the
```stdlib_dir``` path exists and its version matches the interpreter.

"No module named cement" (trusted path): the dependency tree is not
mounted. Add a ```/deps``` mount for the ```site-packages``` root and put it on
```PYTHONPATH```.

"No module named multiprocessing" (trusted path): the WASI stdlib shim is off.
Leave ```wasi_shims``` at its default (```True```).

"No module named tokeo" or your app (trusted path): the tokeo or app
source root is not mounted. The trusted tool is rebuilt from
```tokeo.core.ai.tools...```, so the tokeo source must be on the guest's
```PYTHONPATH``` alongside the app.

A "wrote more than N MB" or "created more than N files" error: the snippet
exceeded ```max_disk_mb``` or ```max_files```. Raise the limit if the tool needs
the space, or check the code is not writing in a loop.

A "timed out after Ns" error: the snippet exceeded ```timeout```. Raise
it, or check the code is not waiting on something that can never happen in the
guest.
