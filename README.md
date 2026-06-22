![tokeo-fundi-header](https://repository-images.githubusercontent.com/1276418849/59604c72-095d-420d-a412-3eaa494e8f73)
<div align="right">image AI created with gemma 4 prompt</div>

<br/>

<h1 align="center">tokeo-fundi</h1>

<p align="center">
  <strong>The governed AI runtime for Tokeo: the model plans, the pipeline governs, the tools compute.</strong>
</p>
<p align="center">
  -- Typed contracts, guarded tool execution, full traces -- from a 1.5 MB local micro model up to any large provider --
</p>

<br/>

## 🚀 What Fundi is

One sentence carries the whole design: **fundi** (the master) wields the tools, **akili** (the mind) plans them, and **tokeo** (the result) is what they produce together.

**Fundi** is the AI extension for [Tokeo](https://github.com/tokeo/tokeo). It adds a complete, compact agent runtime built on one conviction: **the model plans, the pipeline governs, the tools compute.** No step is implicit, every step is inspectable.

Where Tokeo gives you the event-driven CLI framework, Fundi gives that framework a governed way to talk to AI providers: typed message and tool contracts, an agent pipeline where every tool call passes through guards, sandboxes that contain what the tools do, and a full trace of every run. The same agent definitions drive a deterministic mock provider, a model you train yourself, and any large OpenAI-compatible endpoint -- switching between them is editing a profile, not refactoring code.

Fundi is the source-available core of that runtime: the code under `tokeo.core.ai`, its tests under `tests/core/ai`, and the extension `tokeo.ext.ai` that wires it into an application. It lives in the shared `tokeo` namespace, is installed as part of Tokeo, inherits Tokeo's `tokeo` command, and you reach it through `tokeo ai ...`. The concrete tools, guards, and providers a project builds on top of fundi -- including the akili model below -- are generated into your own application and belong to you.

<br/>

## 💪 Why a governed runtime

Most ways of wiring an application to an LLM let the model reach straight into your system. Fundi puts a pipeline in between, and makes every part of it explicit and typed.

- **Contracts first.** Messages, tool calls, results, and traces are typed values (`tokeo.core.ai`), independent of any provider SDK. The contracts are the product; the providers plug into them.
- **Agents are configuration.** An agent is a named composition in YAML -- its tools, its guard chain, its sandbox chain, its step budget. `audited` records everything and forbids nothing; `guarded` adds schema validation and policy (for example a read-only filesystem). Lean by default: with no agent a request runs plain and untraced, and you opt into governance.
- **Tools are plain functions.** Registered with a spec, activated in named groups (calendar, filesystem, mathematics) per profile. The provider never executes anything itself; a tool's result returns as feedback through the guards.
- **Guards form the pipeline.** A before-guard may deny a call ahead of execution; an after-guard observes every outcome, so even a denial is recorded. A denied call is not executed -- the loop continues and the model sees the denial, so it can correct itself.
- **Sandboxes contain execution.** A guard decides *whether* a call may run; a sandbox is the wall it runs *behind*. Fundi ships in-process (zero isolation), subprocess (fault and resource isolation), and a WebAssembly sandbox with no network, explicit mounts only, and a hard memory cap -- the safe home for model-generated code.
- **Trace is the single source of truth.** Every run leaves a typed, inspectable trace: the prompts, the calls, the guard decisions, the results. Honest by construction -- a pipeline that denies nothing still says so.

<br/>

## Getting started

Fundi is installed through Tokeo's `fundi` extra:

```bash
# Install Tokeo with the Fundi AI extension
pip install tokeo[fundi]

# Verify
tokeo --help
```

When you generate a Tokeo project and enable the AI feature, the project is scaffolded with the AI configuration, example tools, and the agent definitions already in place.

```bash
# Generate a project with the AI branch
tokeo generate project your_app
```

<br/>

## A first look

The mock provider needs no server and no network -- it is the deterministic driver that makes the whole pipeline testable and demonstrable:

```bash
# Ask through the default profile (mock provider)
your_app ai ask "ping"

# Run the guarded agent: every tool call passes validate, policy, audit
your_app ai ask "add 14 days to 2026-06-08" --agent guarded

# Watch a policy denial: the readonly agent may read but not write
your_app ai ask "read_file notes.txt" --agent guarded
your_app ai ask "append_file hello"    --agent guarded   # denied, and the model is told why

# Inspect the full trace as JSON
your_app ai ask "add 14 days to 2026-06-08" --agent guarded --json
```

<br/>

## akili -- a model your project owns

Fundi's contracts are provider-shaped, and the proof that they hold all the way down is **akili**: a real, trained micro language model that a generated project owns, trains, and operates itself. akili is **not** part of the fundi package -- it ships as a template, generated into your application under `your_app/core/akili/`, so the weights and the language belong to you. It is the canonical example of writing a provider against Fundi's contracts.

akili does one thing, and does it exactly: it turns a natural-language request -- English or German, plain or nested -- into a **plan** of tool calls over the project's calendar toolset. Nested requests like *"the weekday of today plus 2 days"* become real three-step chains. It runs in-process with plain NumPy: no host to start, no network, no third-party weights, answers in tens of milliseconds, and is fully audited under `--agent guarded` like any other provider.

- **A few hundred thousand parameters, learned from scratch** on the project's own synthetic data -- a byte-level tokenizer and a small transformer, small enough to read in an afternoon.
- **Train first, no shipped weights.** `python -m your_app.core.akili.train` builds the model on your machine (CPU is fine) in a few minutes and reports an honest held-out accuracy; until then the `akili` profile raises a clear hint and its tests skip.
- **The language is data.** Every word and sentence pattern lives in `AKILI-LEX.yaml`; the training-data generator reads it, so teaching akili new language is editing that file and retraining. Capability lives in the data, not in the code -- an ablation switch demonstrates the lesson live.
- **It complements the mock.** The built-in `mock` provider is the deliberately dumb test double that proves the machinery without any prerequisite; akili is the content that proves a project can own its model. The agents stay model-free compositions: the same `audited` or `guarded` agent runs against `mock`, `akili`, or a remote profile unchanged.

```bash
# Train the weights once, then plan tool calls with the model
python -m your_app.core.akili.train
your_app ai ask "der wochentag von heute" --profile akili
your_app ai ask "weekday of 2026-12-24" --profile akili --json

# its signature move: a nested request becomes a real three-step chain
your_app ai ask "the weekday of today plus 14 days" --profile akili --agent guarded
```

The generated project carries akili's full documentation, which is the best place to go deeper:

- **`core/akili/AKILI-LLM.md`** -- how the model works: training, the anatomy of the weights, and grammar-constrained decoding, with diagrams.
- **`core/akili/AKILI-USE.md`** -- what it does: a guided three-act demo of the Fundi agent, the trained model, and -- on purpose -- where the model breaks and why.
- **`core/akili/AKILI-LEX.yaml`** -- the language itself: every word and phrasing the model is taught, ready to edit and extend.

<br/>

## Providers, one pipeline

The runtime is provider-shaped: the same agents and guards run against every backend, so changing where the intelligence comes from is a profile edit.

- **mock** -- the deterministic local provider; no server, no network. The universal test and demo driver.
- **akili** -- the trained micro model, running in-process with plain NumPy.
- **oai_compat** -- any OpenAI-compatible endpoint you run yourself (Ollama, vLLM, llama.cpp, MLX) or a commercial API. The API key resolves through Tokeo's config the normal way -- plain text, a `${ENV_VAR}`, or a `!vault:`-encrypted value -- so the secret need never sit in clear text.

```bash
# Drive a real local or remote model behind the same agents
your_app ai ask "summarize this file" --profile assistant
```

<br/>

## Code mode and the WebAssembly sandbox

For agents that write and run code, Fundi provides a `python_untrusted_exec` tool that runs model-generated Python inside a WebAssembly guest -- no network, explicit mounts only, a hard memory cap and wall-clock timeout on every platform. The model writes the code; the sandbox isolates it.

```bash
# The mock synthesizes code that runs in the wasm guest (needs the ./wasm build)
your_app ai ask "text upper der wochentag von heute" --agent coder
```

The wasm build is opt-in and documented in the project's WASM guide; without it, the relevant calls fail with a clear message rather than running unsandboxed.

<br/>

## How Fundi relates to Tokeo

- **Tokeo** is the event-driven CLI framework: the command surface, messaging, scheduling, automation, gRPC, web, vault, and the project generator. It also ships all the project templates -- including the AI branch and the akili lab that gets generated into your application.
- **Fundi** (`tokeo-fundi`) is the source-available AI runtime, and exactly three things make it up: the code under `tokeo.core.ai`, its tests under `tests/core/ai`, and the extension `tokeo.ext.ai`.
- **Akili** is generated by Tokeo within a project template and placed into your application under `your_app/core/akili/` when selected during your setup.
- **Licensing**: Tokeo itself stays open (MIT), and the AI extension and modules as well as Akili carry a license that keeps the project sustainable while leaving the vast majority of users free. The full terms are in [LICENSE.md](LICENSE.md).

Tokeo runs without Fundi. Fundi depends on Tokeo and is installed through `tokeo[fundi]`. The two share the `tokeo` namespace and the `tokeo` command; only their licenses differ. The akili sources are generated from Tokeo's template but stay under the source-available license. The tools, guards, and providers you write yourself on top of Fundi are your own code and your own license choice.

<br/>

## A note on contributions

We keep the human in the loop and use AI as an exoskeleton, not a replacement -- the same conviction that shapes the runtime itself. Purely AI-generated issues or pull requests are not accepted.

<br/>
<br/>

tokeo is built with ❤️ by Tom Freudenberg - governed AI for Python backends.
