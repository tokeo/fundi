"""
The Tokeo ai agent framework.

This package runs an agent loop: a request goes to a chat-completion model, the
model may call tools, the tools run, and their results go back to the model
until it has a final answer. Around that loop sit the pieces that make it safe
and observable -- guards that allow, deny, mask, cap or check each tool call,
sandboxes that wall off where a tool actually runs, and a trace that records
every step. The whole assembly is described in the ```ai``` configuration
section and reached through one handler, ```app.ai```.

The building blocks, each a class you configure or derive from:

- a **provider** (```TokeoAiProvider```) is the transport to a model: given a
    resolved profile it turns messages into a normalized ```ChatResult```. It is
    a dumb, stateless transport, so it is safe to use from several threads at
    once (dramatiq workers, scheduler jobs).
- a **tool** (```TokeoAiTool```) is a function the model may call, with a typed
    schema.
- an **agent** (```TokeoAiAgent```; ```TokeoAiFundiAgent``` is the standard one)
    is the composition root: it owns the tools, the guard pipeline, the sandbox
    chain, and the loop budgets.
- a **guard** (```TokeoAiGuard```) acts at the stages of a tool call -- it can
    deny a call before it runs, observe its result after, or shape what the
    model sees.
- a **sandbox** (```TokeoAiSandbox```) is the wall a tool runs behind; an agent
    lists a chain and a tool runs in the first sandbox that claims it.

A run returns a ```TokeoAiResult``` (the answer, the trace, and the status).
Providers, tools, agents, guards and sandboxes are registered as classes; the
handler instantiates them with the application. A ```type``` in the config is
either a short name from tokeo's registry or a dotted ```module.Class``` path
imported on demand.

```yaml
ai:
  defaults:
    profile: assistant
    agent: guarded
  profiles:
    assistant:
      type: oai_compat
      options:
        model: qwen2.5
        base_url: http://localhost:11434/v1
      agent: guarded
```

The full reference for the ```ai``` configuration -- every section and notation,
the provider parameters, and using the loop from your own code -- is in the
```config``` subpackage (```config/CONFIG.md```).

### Notes

: The local-first case points ```base_url``` at a server the user runs
  themselves (Ollama, llama.cpp, vLLM, MLX). Tokeo talks to that server but
  does not start or manage it.
"""

# the ai error lives in a leaf module (exc.py) so every ai submodule can import
# it top-level without cycling through this facade; re-exported here so the
# short path ``from tokeo.core.ai import TokeoAiError`` keeps working
from tokeo.core.ai.exc import TokeoAiError

# the package facade: the public names live in focused modules (data shapes,
# one base class per concern); import them from here as before
from tokeo.core.ai.data import Usage, ToolCall, ToolResult, ChatResult, Invocation, ChatMessage, TraceStep, TokeoAiStatus, TokeoAiResult
from tokeo.core.ai.context import TokeoAiContext
from tokeo.core.ai.provider import TokeoAiProvider
from tokeo.core.ai.tool import TokeoAiTool
from tokeo.core.ai.agent import TokeoAiAgent, TokeoAiFundiAgent
from tokeo.core.ai.guard import TokeoAiGuard
from tokeo.core.ai.sandbox import TokeoAiSandbox


__all__ = [
    'TokeoAiError',
    'Usage',
    'ToolCall',
    'ToolResult',
    'ChatResult',
    'Invocation',
    'ChatMessage',
    'TraceStep',
    'TokeoAiContext',
    'TokeoAiStatus',
    'TokeoAiResult',
    'TokeoAiProvider',
    'TokeoAiTool',
    'TokeoAiAgent',
    'TokeoAiFundiAgent',
    'TokeoAiGuard',
    'TokeoAiSandbox',
]
