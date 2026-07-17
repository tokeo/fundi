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
the file-bridge mechanics, the two trust models, the pact contract mount, the
WASI standard-library shims, and troubleshooting.


## Contents

- [When to use it](#when-to-use-it)
- [The two python-exec tools](#the-two-python-exec-tools)
- [Installing a WASI Python build into ./wasm](#installing-a-wasi-python-build-into-wasm)
- [Configuration](#configuration)
- [Options](#options)
- [How it runs: the file bridge](#how-it-runs-the-file-bridge)
- [The two trust models in depth](#the-two-trust-models-in-depth)
- [The pact contract mount](#the-pact-contract-mount)
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

Both tools execute Python and return the value the snippet delivers -- either as
its last line (an expression, the way a notebook cell yields a value) or with a
```return```. The tool hands that raw value back; the sandbox layer prepares it
into the model-facing string and the json view the framework builds. The tools
differ only in their trust model, which in turn decides HOW they reach the
guest.

### ```python_untrusted_exec```

For code you must NOT trust (model-generated, foreign). It carries the marker
```wasm_exec_pysnippet = True```, which tells the wasm sandbox to run the ```code```
argument **directly** in the guest without rebuilding the tool there. The guest
therefore rebuilds no tool from tokeo: no framework mount is needed and the
untrusted snippet is walled off from the framework, your app, and the host. It
still uses the small pact contract to shape its result (see the trust models
below), which is dependency-free and changes none of that. This is the safe
default for anything a model writes.

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
project that uses them often can register an alias itself in a
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
    # untrusted: strong isolation, the guest sees its stdlib and the pact
    # contract only -- no framework, no app, no host paths
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
   their guest paths, the pact contract under the ```/pkg``` package root, and
   (by default) the bundled shims at ```/pkg/shims```.
4. Runs the interpreter on the entry script with a scrubbed environment, a hard
   memory cap, and an epoch deadline.
5. The guest entry either runs the ```code``` argument directly (untrusted,
   ```_GUEST_ENTRY_EXEC_PYSNIPPET```) or rebuilds the tool from its dotted path
   and calls it (trusted, ```_GUEST_ENTRY_EXEC_TOOL```); the two are detailed
   under the trust models below. Either way it
   builds a ```ToolResult``` with the pact contract and writes it to
   ```/io/reply.json```. A tool (or snippet) that raises is not a failure of the
   bridge: it is caught around its own call and returned as a normal reply with
   no value and the message in ```state.exception```. A failure of the machinery
   around it -- an unimportable tool, a bad task, an encode error -- is the other
   kind, written as ```{"error": ...}```.
6. The host reads ```/io/reply.json```: a normal reply is rebuilt into a
   ```ToolResult```, an ```{"error": ...}``` reply is raised as a
   ```TokeoAiError```.

Only JSON-able arguments and the result's two string views (```as_str``` and
```as_json```) cross the bridge; the host rebuilds ```as_data``` from
```as_json``` on the other side, so a value's structured form survives without
the guest having to send a possibly non-JSON-able object. The tool's own
```print()``` output is captured and folded into ```state.stdout``` rather than
left to corrupt the single JSON reply. The guest's stderr is captured too, so a
failed run reports why the interpreter aborted instead of a bare wasm backtrace.

The whole path, host and guest across the file bridge, at a glance -- the
host writes the task and picks the entry, the guest runs it and writes the
reply, the host turns that reply back into a ```ToolResult``` (or raises on a
machinery error):

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
  <text x="180" y="264" text-anchor="middle" font-size="12" fill="#0C447C">/pkg pact, /pkg/shims</text>

  <path d="M300 241 L378 241" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="380" y="204" width="240" height="74" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="500" y="226" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">entry.py runs</text>
  <text x="500" y="246" text-anchor="middle" font-size="12" fill="#3C3489">pysnippet: run_snippet</text>
  <text x="500" y="264" text-anchor="middle" font-size="12" fill="#3C3489">tool: load + tool.exec</text>

  <line x1="500" y1="278" x2="500" y2="304" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="380" y="306" width="240" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="500" y="329" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">import pact via /pkg</text>
  <text x="500" y="347" text-anchor="middle" font-size="12" fill="#3C3489">build value/state reply</text>

  <line x1="500" y1="362" x2="500" y2="386" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="380" y="388" width="240" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="500" y="413" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">write /io/reply.json</text>

  <path d="M378 410 L302 410" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="388" width="240" height="44" rx="8" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <text x="180" y="413" text-anchor="middle" font-size="14" font-weight="500" fill="#042C53">read /io/reply.json</text>

  <line x1="150" y1="432" x2="150" y2="456" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="458" width="240" height="48" rx="8" fill="#FCEBEB" stroke="#A32D2D" stroke-width="0.5"/>
  <text x="180" y="479" text-anchor="middle" font-size="14" font-weight="500" fill="#501313">error to TokeoAiError</text>
  <text x="180" y="496" text-anchor="middle" font-size="12" fill="#791F1F">machinery failure (B)</text>

  <line x1="210" y1="432" x2="210" y2="524" stroke="#888780" stroke-width="1.5" marker-end="url(#ah3)"/>
  <rect x="60" y="526" width="240" height="48" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="180" y="547" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">_rebuild to ToolResult</text>
  <text x="180" y="564" text-anchor="middle" font-size="12" fill="#0F6E56">value + state to caller</text>
</svg>


## The two trust models in depth

The difference between the tools is not in the tool code -- it is in which guest
entry the sandbox chooses, based on the tool's ```wasm_exec_pysnippet``` marker.

**Pysnippet path (untrusted).** The guest runs the ```code``` argument as-is. It
rebuilds no tool from tokeo, so the box needs no framework mount, no app, and no
dependency tree. The model-generated code sees only the WASI standard library,
its own snippet, and the pact contract (see below) -- nothing of the framework,
your app, or any host path you did not mount. That is the strongest isolation
the sandbox offers: the code cannot reach the framework even though it can build
a result with it.

The exec-pysnippet guest entry (```_GUEST_ENTRY_EXEC_PYSNIPPET```) in detail: it
imports the pact contract including ```run_snippet```, reads the ```code```
argument, and runs it directly -- no tool is loaded, so no ```importlib```
and no framework. A snippet that raises, fails to parse, or uses an
unsupported form is the same tool error (A); the value/state reply and the
machinery error (B) are built exactly as on the exec-tool path:

<svg width="100%" viewBox="0 0 680 520" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" role="img" aria-label="Flow of the exec-pysnippet guest entry">
  <defs>
    <marker id="ah2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <rect x="240" y="30" width="200" height="50" rx="8" fill="#F1EFE8" stroke="#888780" stroke-width="0.5"/>
  <text x="340" y="50" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">import pact</text>
  <text x="340" y="68" text-anchor="middle" font-size="12" fill="#5F5E5A">+ run_snippet (no importlib)</text>

  <line x1="340" y1="80" x2="340" y2="98" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="100" width="200" height="56" rx="8" fill="#F1EFE8" stroke="#888780" stroke-width="0.5"/>
  <text x="340" y="123" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">read task, get code</text>
  <text x="340" y="141" text-anchor="middle" font-size="12" fill="#5F5E5A">arguments['code']</text>

  <line x1="340" y1="156" x2="340" y2="174" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="176" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="199" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">run_snippet(code, ns)</text>
  <text x="340" y="217" text-anchor="middle" font-size="12" fill="#3C3489">under stdout-capture</text>

  <rect x="40" y="180" width="170" height="48" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="125" y="201" text-anchor="middle" font-size="14" font-weight="500" fill="#412402">snippet fails = A</text>
  <text x="125" y="218" text-anchor="middle" font-size="12" fill="#633806">raise/parse/await</text>
  <line x1="240" y1="204" x2="212" y2="204" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>

  <line x1="340" y1="232" x2="340" y2="250" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="252" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="275" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">None or wrap value</text>
  <text x="340" y="293" text-anchor="middle" font-size="12" fill="#3C3489">create_tool_result</text>

  <path d="M125 228 L125 280 L238 280" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>

  <line x1="340" y1="308" x2="340" y2="326" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="328" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="351" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">fold stdout, encode</text>
  <text x="340" y="369" text-anchor="middle" font-size="12" fill="#3C3489">value as_str/as_json, state</text>

  <line x1="340" y1="384" x2="340" y2="450" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <rect x="240" y="452" width="200" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="477" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">write /io/reply.json</text>

  <rect x="470" y="176" width="170" height="64" rx="8" fill="#FCEBEB" stroke="#A32D2D" stroke-width="0.5"/>
  <text x="555" y="197" text-anchor="middle" font-size="14" font-weight="500" fill="#501313">outer except = B</text>
  <text x="555" y="215" text-anchor="middle" font-size="12" fill="#791F1F">task/encode/json fail</text>
  <text x="555" y="231" text-anchor="middle" font-size="12" fill="#791F1F">dict(error=...)</text>
  <path d="M440 128 L555 128 L555 174" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
  <path d="M555 240 L555 474 L442 474" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah2)"/>
</svg>

**Tool path (trusted).** The guest imports the tool by its dotted path
(```tokeo.core.ai.tools...```) and calls it. That import chain needs tokeo, the
app, and their dependencies present on the guest's ```PYTHONPATH```, so the trusted
sandbox mounts those trees read-only. The cost is real and intentional: every
dependency the rebuilt tool pulls in must be mounted, and the executed code can
read all of it. That visibility is the price of "may use the framework", and it
is exactly why this path is for trusted input only.

The exec-tool guest entry (```_GUEST_ENTRY_EXEC_TOOL```) in detail: it
imports the pact contract, reads the task, rebuilds the tool with ```app=
None``` and calls its ```exec``` under an stdout capture. A tool that raises
is caught as a tool error (A) -- a result with no value and the message in
```state.exception```; anything outside that call -- the load, the encode --
is a machinery error (B):

<svg width="100%" viewBox="0 0 680 560" xmlns="http://www.w3.org/2000/svg" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif" role="img" aria-label="Flow of the exec-tool guest entry">
  <defs>
    <marker id="ah" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="#888780" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <rect x="240" y="30" width="200" height="50" rx="8" fill="#F1EFE8" stroke="#888780" stroke-width="0.5"/>
  <text x="340" y="50" text-anchor="middle" font-size="14" font-weight="500" fill="#2C2C2A">import pact</text>
  <text x="340" y="68" text-anchor="middle" font-size="12" fill="#5F5E5A">data, tool (via /pkg)</text>

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
  <text x="340" y="281" text-anchor="middle" font-size="12" fill="#3C3489">under stdout-capture</text>

  <rect x="40" y="244" width="170" height="48" rx="8" fill="#FAEEDA" stroke="#854F0B" stroke-width="0.5"/>
  <text x="125" y="265" text-anchor="middle" font-size="14" font-weight="500" fill="#412402">tool raises = A</text>
  <text x="125" y="282" text-anchor="middle" font-size="12" fill="#633806">value None, exception</text>
  <line x1="240" y1="268" x2="212" y2="268" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>

  <line x1="340" y1="296" x2="340" y2="314" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="316" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="339" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">normalize result</text>
  <text x="340" y="357" text-anchor="middle" font-size="12" fill="#3C3489">None / wrap / passthrough</text>

  <path d="M125 292 L125 344 L238 344" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>

  <line x1="340" y1="372" x2="340" y2="390" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="392" width="200" height="56" rx="8" fill="#EEEDFE" stroke="#534AB7" stroke-width="0.5"/>
  <text x="340" y="415" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">fold stdout, _encode</text>
  <text x="340" y="433" text-anchor="middle" font-size="12" fill="#3C3489">value as_str/as_json, state</text>

  <line x1="340" y1="448" x2="340" y2="488" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <rect x="240" y="490" width="200" height="44" rx="8" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="515" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">write /io/reply.json</text>

  <rect x="470" y="240" width="170" height="64" rx="8" fill="#FCEBEB" stroke="#A32D2D" stroke-width="0.5"/>
  <text x="555" y="261" text-anchor="middle" font-size="14" font-weight="500" fill="#501313">outer except = B</text>
  <text x="555" y="279" text-anchor="middle" font-size="12" fill="#791F1F">load/encode/json fail</text>
  <text x="555" y="295" text-anchor="middle" font-size="12" fill="#791F1F">dict(error=...)</text>
  <path d="M440 192 L555 192 L555 238" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
  <path d="M555 304 L555 512 L442 512" fill="none" stroke="#888780" stroke-width="1.5" marker-end="url(#ah)"/>
</svg>

A useful way to read the contrast: the untrusted path mounts only the contract
it needs to shape a result and asks no further questions; the trusted path
mounts several trees and makes each grant of visibility explicit in the
configuration. Both behaviors are the same deny-by-default principle seen from
opposite ends.


## The pact contract mount

Both guest entries build their reply the way the host would: with
```create_tool_result``` from the ```tokeo.pact``` contract, and they deliver
the snippet's value through the same contract (its last line or a ```return```).
So the sandbox makes pact importable in every guest, on **both** paths -- the
untrusted one too.

This does not weaken the isolation. The pact contract is deliberately tiny and
dependency-free: the result dataclasses, a pure json encode, and the snippet
wrap, with nothing from the framework, no IO, no network, no secrets, and no
import beyond the standard library and itself. Mounting it is the same kind of
grant as the ```/io``` json hand-off point -- a narrow, inert surface the guest
reads to shape a result, not a door into tokeo. The untrusted snippet can see
the shape of a ```ToolResult``` and the encoder that builds it; it gains nothing
it could turn against the host.

The mount cannot be a single leaf, because ```tokeo.pact.*``` is a **dotted**
import: the guest must be able to walk ```tokeo``` then ```pact``` then the
subpackage as real directories. ```/lib``` is the user's stdlib mount and lists
no ```tokeo``` child, so a leaf at ```/lib/tokeo/pact/<name>``` would fail at the
very first step of that walk. Instead the sandbox builds a small package root,
mounted at ```/pkg``` and placed on the guest's ```PYTHONPATH```: it holds the
real namespace chain ```tokeo/pact``` as plain directories (PEP 420 namespace,
no ```__init__```), with an empty stub per subpackage as a mount point, and then
mounts each real subpackage over its stub.

```tokeo.pact``` is a split namespace package: ```tokeo.pact.utils``` ships in
tokeo, ```tokeo.pact.ai``` in fundi, each at its own single location on disk.
The sandbox walks the namespace and mounts every subpackage it finds at
```/pkg/tokeo/pact/<name>```, so a future pact subpackage is carried into the
guest with no change to the sandbox.


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
a clear error rather than silently doing nothing. The shims are ours, like the
pact contract, so they live under the ```/pkg``` package root rather than inside
the user's stdlib: mounted read-only at ```/pkg/shims``` and placed first on
```PYTHONPATH```, so they stand in for the absent modules, controlled by
```shim_wasi_stdlib``` (on by default).

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
