"""
Data shapes of the ai subsystem: the typed objects that travel the run.

Every object a run produces is a typed value recorded onto one trace: the chat
messages (```ChatMessage```), the tool invocations (```Invocation```), the model
results (```ChatResult```). ```ChatMessage``` is a ```dict``` subclass, so it is
still the OpenAI-style message the provider receives, while carrying a type the
trace and the context's typed views group it by.
"""

from datetime import datetime as datetime_type, timezone
from dataclasses import dataclass, field


class ChatMessage(dict):
    """
    One entry of the chat conversation -- a ```dict``` that carries a type.

    Messages are OpenAI-style dicts (```role``` plus ```content``` and, for an
    assistant tool-call turn, ```tool_calls```; for a tool turn,
    ```tool_call_id```). ```ChatMessage``` *is* such a dict, so the provider
    receives exactly what it always did, but as a typed object it can sit on the
    run's trace and be grouped into the context's ```messages``` view. It covers
    every role (```user```, ```assistant```, ```tool```), so the name stays
    neutral rather than naming one direction.

    Construct it like a dict: ```ChatMessage(role='user', content='hi')``` or
    ```ChatMessage({'role': 'user', 'content': 'hi'})```.

    """


@dataclass
class Usage:
    """
    Token usage reported for a single chat call.

    ### Args

    - **prompt_tokens** (int): Tokens consumed by the prompt
    - **completion_tokens** (int): Tokens produced in the completion
    - **total_tokens** (int): Total as reported by the provider

    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ToolCall:
    """
    A single tool call requested by the model.

    ### Args

    - **id** (str): Provider-assigned id, echoed back with the tool result
    - **name** (str): Name of the tool the model wants to call
    - **arguments** (dict): Parsed arguments for the call

    """

    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ToolValue:
    """
    The value a tool delivered, in prepared views.

    A ```ToolResult``` holds a ```ToolValue``` when the tool delivered
    something, and ```None``` when it did not. So the mere existence answers
    "is there a value?" -- ```if result.value:``` reads as "did the tool deliver
    a value", not a truthiness test of the value (a delivered ```0```/```""```
    still has a value object and so stays truthy).

    ### Args

    - **as_str** (str): The value as a string, the model-facing form
    - **as_json** (str): The value as a json string, built with the framework
        encoder so dates and dataclasses survive
    - **as_data** (object): The value as a structured object for the trace or a
        ui (the former ```data``` field), kept out of the message history

    """

    as_str: str = ''
    as_json: str = ''
    as_data: object = None


@dataclass
class ToolStates:
    """
    Facts about a tool run, filled by trusted framework or tool code.

    Never written by executed untrusted content: the tool is the trust
    boundary; the executed code writes only its value, the surrounding tool code
    observes the run and fills these states.

    ### Args

    - **incomplete** (bool): The json form is not faithful to the value (the
        encoder had to substitute a representation for something it could not
        render); ```False``` when the json form represents the value faithfully
    - **stdout** (str | None): Informal output of the run -- captured print
        output of executed code, or a note the tool set on purpose; ```None```
        when there was none
    - **stderr** (str | None): Informal error output of the run; ```None```
        when there was none
    - **exception** (str | None): The exception that the executed code raised,
        as ```type: message```; ```None``` when the run did not raise

    """

    incomplete: bool = False
    stdout: object = None
    stderr: object = None
    exception: object = None


@dataclass
class ToolResult:
    """
    Result a tool produced, assembled by the framework, not by the tool.

    A tool returns a plain value (or ```None```), and the framework turns it
    into this transport object. A tool that wants finer control may build one
    itself with ```create_tool_result``` (see ```tokeo.core.ai.tool```).

    ### Args

    - **value** (ToolValue | None): The delivered value in prepared views, or
        ```None``` when the tool returned nothing. Existence answers "is there a
        value?", truthy for any delivered value including ```0```/```""```
    - **state** (ToolStates): Facts about the run (see ```ToolStates```)

    """

    value: object = None  # a ToolValue, or None when nothing was returned
    state: ToolStates = field(default_factory=ToolStates)


@dataclass
class ChatResult:
    """
    Normalized result of a chat call, uniform across providers.

    ### Args

    - **text** (str): Assistant message content; may be empty on a turn that
        only requests tool calls
    - **reasoning** (str): The model's reasoning/thinking, when available;
        kept separate from the answer text. A non-standard field that only some
        endpoints (DeepSeek-R1, QwQ and similar local reasoning models) report
        under ```reasoning```/```reasoning_content```; empty when absent, so it
        is harvested when present, never required
    - **refusal** (str): The model's explicit refusal message, when it declines
        rather than answers (structured-outputs ```refusal``` field); empty
        otherwise. Distinct from an empty ```text```, so a caller can tell
        "declined" from "no content"
    - **tool_calls** (list): The ```ToolCall``` entries the model requested
    - **usage** (Usage | None): Token usage, when the provider reports it
    - **finish_reason** (str | None): Why the model stopped, when reported
    - **system_fingerprint** (str | None): The backend configuration fingerprint
        the endpoint reports, when present; with a fixed seed it identifies the
        exact backend state, so a changed value explains differing outputs
    - **raw** (dict | None): The unmodified provider response, kept so a
        caller can always inspect exactly what came back

    """

    text: str = ''
    reasoning: str = ''
    refusal: str = ''
    tool_calls: list = field(default_factory=list)
    usage: Usage | None = None
    finish_reason: str | None = None
    system_fingerprint: str | None = None
    raw: dict | None = None


@dataclass
class Invocation:
    """
    A single tool call as it travels the tool stations of the loop.

    Built by the handler for each requested tool call and tracked onto the run's
    trace at creation, then handed to the guards: an ```on_call``` guard may set
    ```decision```/```reason``` to block it, the tool runs unless denied, and an
    ```on_return``` guard sees the ```result``` or ```error```. The object is
    mutable on purpose, so a guard can refine the outcome in place (a later
    redact/truncate guard rewrites ```result```).

    ### Args

    - **id** (str): The provider-assigned tool-call id, echoed in the result
    - **name** (str): The tool the model wants to call
    - **arguments** (dict): The parsed arguments for the call
    - **parameters** (dict | None): The called tool's declared parameters
        schema, attached by the handler so an ```on_call``` guard can validate
        the arguments; ```None``` when the tool is unknown
    - **decision** (str): ```ALLOW``` or ```DENY``` (the constants below); an
        ```on_call``` guard may set it to deny the call
    - **reason** (str | None): Why a guard denied or flagged the call
    - **result** (ToolResult | None): The tool's result when it ran
    - **error** (str | None): The error text when the tool raised
    - **sandbox** (str | None): The configured name of the sandbox the tool
        ran in (e.g. ```in_process```, ```jailed```, ```wasm_untrusted```), so
        the trace shows WHERE each call executed -- the honest-tier record.
        ```None``` until the call reaches a sandbox (a denied call never does)

    """

    # the decision values a guard sets / the loop reads, named here at the
    # concept (the invocation's decision field) so call sites use a constant,
    # not a bare string literal that a typo could silently break
    ALLOW = 'allow'
    DENY = 'deny'

    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    parameters: dict | None = None
    decision: str = ALLOW
    reason: str | None = None
    result: ToolResult | None = None
    error: str | None = None
    sandbox: str | None = None


@dataclass
class TraceStep:
    """
    One step on the run's trace: an origin and the object it left in hand.

    The trace is a list of these -- the full, ordered history of the run for
    audit and analysis. Every recording makes one: ```track``` makes a step for
    a fresh object (the loop is the origin), ```supersede``` makes one for each
    guard that ran (the guard is the origin). So the trace shows *who* acted and
    *what* the object looked like after, step by step.

    The caches (```invocations```/```messages```/```results```) are a different
    thing: lists of the bare objects in their current state. The trace is the
    history of steps; the caches are the latest state by kind. A step is never a
    cache entry and a cache entry is never a step.

    ### Args

    - **origin**: Who produced this step -- the guard at a ```supersede```, the
        loop (its handler) at a ```track```. Never ```None```, so every step is
        attributable
    - **object**: The run object as it stood after this step (a ```ChatMessage```,
        ```Invocation```, ```ChatResult```, ...)
    - **changed** (bool): Whether this step introduced a *new* object (a guard
        returned a fresh copy) rather than leaving the existing one in place. A
        ```track``` is always a new object; a ```supersede``` is ```True``` only
        when the guard handed back a different object
    - **at** (datetime): When the step was recorded, a UTC timestamp; the json
        encoder renders it as a UTC time string
    - **stage** (str | None): The guard stage that produced this step
        (```on_begin```, ```on_call```, ...), or ```None``` for a loop ```track```
        that belongs to no stage (a fresh message/result the loop recorded)

    """

    origin: object
    object: object
    changed: bool = True
    # WHY a default factory: each step stamps its own creation time, so a
    # field default (shared) would be wrong; utc_now is tz-aware UTC, and the
    # json encoder formats it via to_utc_timestring
    at: object = field(default_factory=lambda: datetime_type.now(timezone.utc))
    # the guard stage that recorded this step; None for a loop track (a fresh
    # object the loop added outside any stage)
    stage: object = None


@dataclass
class TokeoAiStatus:
    """
    The loop's own counters for one run.

    Separated from the run's data (the trace and its views) so the bookkeeping
    that bounds the loop has its own small home and can grow (a future stop
    reason, a final flag) without crowding the data.

    ### Args

    - **steps** (int): The number of tool rounds run so far, against
        ```max_steps```
    - **failed_loops** (int): Consecutive rounds with no successful tool call,
        against ```max_loops```; reset by any successful call

    """

    steps: int = 0
    failed_loops: int = 0


@dataclass
class TokeoAiResult:
    """
    The result of a whole ```chat``` run -- answer, history, and bookkeeping.

    What ```chat``` returns. It separates the three views of one run that used
    to be crammed onto a single ```ChatResult```:

    - the **answer**: the final model reply, a ```ChatResult``` (text, refusal,
        reasoning, tool_calls, usage, raw). The answer is not always plain text
        -- a refusal is distinct from empty text -- so the full ```ChatResult```
        is kept, not a flattened string.
    - the **trace**: the ordered ```TraceStep``` history of the run (every
        model round, tool call, and guard that ran).
    - the **status**: the loop's counters (```steps```, ```failed_loops```).

    The trace and status live on the context during the run; ```chat``` hands
    them out here so the caller has them without reaching into the context (now
    gone) -- and without the answer carrying its own trace, which made the
    answer enclose itself.

    ### Args

    - **answer** (ChatResult): The final model reply in full
    - **trace** (list): The run's ```TraceStep``` history, in order
    - **status** (TokeoAiStatus): The loop's counters for the run

    """

    answer: ChatResult = field(default_factory=ChatResult)
    trace: list = field(default_factory=list)
    status: TokeoAiStatus = field(default_factory=TokeoAiStatus)
