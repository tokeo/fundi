"""
Data shapes of the ai subsystem: the typed objects that travel the run.

Every object a run produces is a typed value recorded onto one trace: the chat
messages (```TokeoAiChatMessage```), the tool invocations (```Invocation```), the
model results (```TokeoAiChatResult```). ```TokeoAiChatMessage``` is a ```dict```
subclass, so it is still the OpenAI-style message the provider receives, while
carrying a type the trace and the context's typed views group it by.
"""

from datetime import datetime as datetime_type, timezone
from dataclasses import dataclass, field


class TokeoAiChatMessage(dict):
    """
    One entry of the chat conversation, a ```dict``` that carries a type.

    Messages are OpenAI-style dicts (```role``` plus ```content``` and, for an
    assistant tool-call turn, ```tool_calls```; for a tool turn,
    ```tool_call_id```). ```TokeoAiChatMessage``` *is* such a dict, so the provider
    receives exactly what it always did, but as a typed object it can sit on the
    run's trace and be grouped into the context's ```messages``` view. It covers
    every role (```user```, ```assistant```, ```tool```), so the name stays
    neutral rather than naming one direction.

    Construct it like a dict: ```TokeoAiChatMessage(role='user', content='hi')``` or
    ```TokeoAiChatMessage({'role': 'user', 'content': 'hi'})```.

    """


@dataclass
class TokeoAiUsage:
    """
    Token usage reported for a single chat call.

    ### Args

    - **prompt_tokens** (int): Tokens consumed by the prompt
    - **completion_tokens** (int): Tokens produced in the completion
    - **total_tokens** (int): Total as reported by the provider

    """

    #: Tokens consumed by the prompt
    prompt_tokens: int = 0
    #: Tokens produced in the completion
    completion_tokens: int = 0
    #: Total as reported by the provider
    total_tokens: int = 0


@dataclass
class TokeoAiToolCall:
    """
    A single tool call requested by the model.

    ### Args

    - **id** (str): Provider-assigned id, echoed back with the tool result
    - **name** (str): Name of the tool the model wants to call
    - **arguments** (dict): Parsed arguments for the call; what the model sent
        that is no JSON object is kept as it came, the loop refuses the call

    """

    #: Provider-assigned id, echoed back with the tool result
    id: str
    #: Name of the tool the model wants to call
    name: str
    #: Parsed arguments for the call
    arguments: dict = field(default_factory=dict)


@dataclass
class ToolValue:
    """
    The value a tool delivered, in prepared views.

    A ```ToolResult``` holds a ```ToolValue``` when the tool delivered
    something, and ```None``` when it did not. So the mere existence answers
    "is there a value?"; ```if result.value:``` reads as "did the tool deliver
    a value", not a truthiness test of the value (a delivered ```0```/```""```
    still has a value object and so stays truthy).

    ### Args

    - **as_str** (str): The value as a string, the model-facing form
    - **as_json** (str): The value as a json string, built with the framework
        encoder so dates and dataclasses survive
    - **as_data** (object): The value as a structured object for the trace or a
        ui (the former ```data``` field), kept out of the message history

    """

    #: The value as a string, the model-facing form
    as_str: str = ''
    #: The value as a json string
    as_json: str = ''
    #: The value as a structured object for the trace or a ui
    as_data: object = None


@dataclass
class ToolStates:
    """
    Facts about a tool run, filled by the framework, the tool, or the sandbox.

    In process the surrounding tool code fills them. Behind a process or wasm
    wall they come back with the value and are a hint, not a trusted fact:
    the wasm guest reports ```is_incomplete```, and ```error``` is the last line
    of its stderr on a failed exit (see the wasm sandbox for what the host
    observes itself).

    ### Args

    - **stdout** (str|None): Informal output of the run: captured print
        output of executed code, or a note the tool set on purpose; ```None```
        when there was none
    - **stderr** (str|None): Informal error output of the run; ```None```
        when there was none
    - **error** (str|None): The exception that the executed code raised,
        as ```type: message```; ```None``` when the run did not raise
    - **is_incomplete** (bool): The json form is not faithful to the value (the
        encoder had to substitute a representation for something it could not
        render); ```False``` when the json form represents the value faithfully

    """

    #: (str|None) Informal output of the run
    stdout: object = None
    #: (str|None) Informal error output of the run
    stderr: object = None
    #: (str|None) The exception the executed code raised, as ```type: message```
    error: object = None
    #: The json form is not faithful to the value
    is_incomplete: bool = False


@dataclass
class ToolResult:
    """
    Result a tool produced, assembled by the framework, not by the tool.

    A tool returns a plain value (or ```None```), and the framework turns it
    into this transport object. A tool that wants finer control may build one
    itself with ```create_tool_result``` (see ```tokeo.core.ai.tool```).

    ### Args

    - **value** (ToolValue|None): The delivered value in prepared views, or
        ```None``` when the tool returned nothing. Existence answers "is there a
        value?", truthy for any delivered value including ```0```/```""```
    - **state** (ToolStates): Facts about the run (see ```ToolStates```)

    """

    #: (ToolValue|None) The delivered value in prepared views, or ```None```
    value: object = None
    #: Facts about the run
    state: ToolStates = field(default_factory=ToolStates)


@dataclass
class TokeoAiChatResult:
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
    - **tool_calls** (list): The ```TokeoAiToolCall``` entries the model requested
    - **usage** (TokeoAiUsage|None): Token usage, when the provider reports it
    - **finish_reason** (str|None): Why the model stopped, when reported
    - **system_fingerprint** (str|None): The backend configuration fingerprint
        the endpoint reports, when present; with a fixed seed it identifies the
        exact backend state, so a changed value explains differing outputs
    - **raw** (dict|None): The unmodified provider response, kept so a
        caller can always inspect exactly what came back

    """

    #: Assistant message content
    text: str = ''
    #: The model's reasoning, when available
    reasoning: str = ''
    #: The model's explicit refusal message, when it declines
    refusal: str = ''
    #: The ```TokeoAiToolCall``` entries the model requested
    tool_calls: list = field(default_factory=list)
    #: Token usage, when the provider reports it
    usage: TokeoAiUsage | None = None
    #: Why the model stopped, when reported
    finish_reason: str | None = None
    #: The backend configuration fingerprint, when reported
    system_fingerprint: str | None = None
    #: The unmodified provider response
    raw: dict | None = None


@dataclass
class Invocation:
    """
    A single tool call as it travels the tool stages of the loop.

    Built by the handler for each requested tool call and tracked onto the run's
    trace at creation, then handed to the guards: an ```on_call``` guard may set
    ```decision```/```reason``` to deny it, the tool runs unless denied, and an
    ```on_return``` guard sees the ```result``` or ```error```. The object is
    mutable on purpose, so a guard can refine the outcome in place (a later
    redact/truncate guard rewrites ```result```).

    ### Args

    - **id** (str): The provider-assigned tool-call id, echoed in the result
    - **name** (str): The tool the model wants to call
    - **arguments** (dict): The parsed arguments for the call
    - **parameters** (dict|None): The called tool's declared parameters
        schema, attached by the handler so an ```on_call``` guard can validate
        the arguments; ```None``` when the tool is unknown
    - **decision** (str): ```ALLOW``` or ```DENY``` (the constants below); an
        ```on_call``` guard may set it to deny the call
    - **reason** (str|None): Why a guard denied or flagged the call
    - **sandbox_name** (str|None): The configured name of the sandbox the tool
        ran in (e.g. ```in_process```, ```jailed```, ```wasm_untrusted```), so
        the trace shows where each call executed, the honest-tier record.
        ```None``` until the call reaches a sandbox (a denied call never does)
    - **result** (ToolResult|None): The tool's result when it ran
    - **error** (str|None): The text of a failure of the sandbox machinery
        (timeout, broken reply); a raise of the tool itself lands in
        ```result.state.error```

    """

    # the decision values a guard sets / the loop reads, named here at the
    # concept (the invocation's decision field) so call sites use a constant,
    # not a bare string literal that a typo could silently break
    #: The decision that lets the call run
    ALLOW = 'allow'
    #: The decision that denies the call
    DENY = 'deny'

    #: The provider-assigned tool-call id, echoed in the result
    id: str
    #: The tool the model wants to call
    name: str
    #: The parsed arguments for the call
    arguments: dict = field(default_factory=dict)
    #: The called tool's parameters schema, ```None``` for an unknown tool
    parameters: dict | None = None
    #: ```ALLOW``` or ```DENY```
    decision: str = ALLOW
    #: Why a guard denied or flagged the call
    reason: str | None = None
    #: The configured name of the sandbox the tool ran in
    sandbox_name: str | None = None
    #: The tool's result when it ran
    result: ToolResult | None = None
    #: The text of a failure of the sandbox machinery
    error: str | None = None


@dataclass
class TraceStep:
    """
    One step on the run's trace: an origin and the object it left in hand.

    The trace is a list of these: the full, ordered history of the run for
    audit and analysis. Every recording makes one: ```track``` makes a step for
    a fresh object (the loop is the origin), ```supersede``` makes one for each
    guard that ran (the guard is the origin). So the trace shows *who* acted and
    *what* the object looked like after, step by step.

    The caches (```invocations```/```messages```/```results```) are a different
    thing: lists of the bare objects in their current state. The trace is the
    history of steps; the caches are the latest state by kind. A step is never a
    cache entry and a cache entry is never a step.

    ### Args

    - **origin**: Who produced this step: the guard at a ```supersede```, the
        loop (its handler) at a ```track```. Never ```None```, so every step is
        attributable
    - **object**: The run object as it stood after this step (a
        ```TokeoAiChatMessage```, ```Invocation```, ```TokeoAiChatResult```, ...)
    - **changed** (bool): Whether this step introduced a *new* object (a
        guard returned a fresh copy) rather than leaving the existing one in
        place. A ```track``` is always a new object; a ```supersede``` is
        ```True``` only when the guard handed back a different object
    - **stage** (str|None): The guard stage that produced this step
        (```on_begin```, ```on_call```, ...), or ```None``` for a loop ```track```
        that belongs to no stage (a fresh message/result the loop recorded)
    - **at** (datetime): When the step was recorded, a UTC timestamp; the json
        encoder renders it as a UTC time string

    """

    #: Who produced this step: a governor, or the loop at a ```track```
    origin: object
    #: The run object as it stood after this step
    object: object
    #: Whether this step introduced a new object
    changed: bool = True
    #: (str|None) The stage that recorded this step; ```None``` for a loop
    #: track (a fresh object the loop added outside any stage)
    stage: object = None
    # WHY a default factory: each step stamps its own creation time, so a
    # field default (shared) would be wrong; datetime_type.now(timezone.utc)
    # is tz-aware UTC, and the json encoder formats it via to_utc_timestring
    #: (datetime) When the step was recorded, in UTC
    at: object = field(default_factory=lambda: datetime_type.now(timezone.utc))


@dataclass
class TokeoAiLoopdata:
    """
    The loop's own live counters for one turn.

    Separated from the run's data (the trace and its views) so the bookkeeping
    that limits the loop has its own small home. Created fresh for every turn and
    owned by the loop, never seeded by the caller, so a turn always starts at
    zero and no caller can preset a counter.

    ### Args

    - **steps** (int): The number of tool rounds run so far, against
        ```max_steps```
    - **failed_loop_cnt** (int): Consecutive rounds with no successful tool call,
        against ```max_loops```; reset by any successful call

    """

    #: The number of tool rounds run so far, against ```max_steps```
    steps: int = 0
    #: Consecutive rounds with no successful tool call, against ```max_loops```
    failed_loop_cnt: int = 0


class TokeoAiTurndata(dict):
    """
    A free, shared data area for one turn.

    Created fresh for every turn on the context (```ctx.turndata```) and gone
    with it. It is a working state for this turn, not a cache: nothing is
    persisted. A run starts empty unless the caller seeds it with a
    ```turndata``` (see the context), which lets a delegate share its
    caller's turndata.

    Shared means shared: everyone sees and can overwrite every key. Among
    trusted framework code that is a convention, not a risk. Write under your
    own key (e.g. the governor's name) mostly.
    """


@dataclass
class TokeoAiTraceNote:
    """
    A free note on the trace, so a plain value gets a name.

    The export renders an unknown object as its type name, but a plain value
    (a str, a dict, a number) serializes itself and lands on the trace
    nameless, indistinguishable from real objects and not filterable.
    ```track``` wraps such a value in this note; anything that is a class of its
    own passes through untouched. For a note with more structure, define a class:
    it keeps its own type name on the trace.

    ### Args

    - **content**: The noted value, as handed to ```track```

    """

    #: The noted value, as handed to ```track```
    content: object = None


@dataclass
class TokeoAiRunResult:
    """
    The result of a whole ```chat``` run: answer, history, and bookkeeping.

    What ```chat``` returns. It separates the three views of one run that used
    to be crammed onto a single ```TokeoAiChatResult```:

    - the answer: the final model reply, a ```TokeoAiChatResult``` (text, refusal,
        reasoning, tool_calls, usage, raw). The answer is not always plain text
        (a refusal is distinct from empty text), so the full ```TokeoAiChatResult```
        is kept, not a flattened string.
    - the loopdata: the loop's counters (```steps```, ```failed_loop_cnt```).
    - the trace: the ordered ```TraceStep``` history of the run (every
        model round, tool call, and guard that ran).
    - the confirmations: every confirm decision of the run, in order.

    The trace, the counters and the confirmations live on the context during
    the run; ```chat``` hands them out here so the caller has them without
    reaching into the context (now gone), and without the answer carrying its
    own trace, which made the answer enclose itself. A declined run raises
    ```TokeoAiRunDeclinedError``` instead, with this result (without the
    answer) in its ```result```.

    ### Args

    - **answer** (TokeoAiChatResult): The final model reply in full
    - **loopdata** (TokeoAiLoopdata): The loop's live counters for the run
    - **trace** (list): The run's ```TraceStep``` history, in order
    - **confirmations** (list): Every confirm decision of the run in order,
        each a dict (stage, guard_name, subject_dict, previous_decision,
        previous_reason, answer, decision, note, by, who, retries, recalled,
        at as a UTC ```datetime```), for the app to keep and learn from

    """

    #: The final model reply in full
    answer: TokeoAiChatResult = field(default_factory=TokeoAiChatResult)
    #: The loop's live counters for the run
    loopdata: TokeoAiLoopdata = field(default_factory=TokeoAiLoopdata)
    #: The run's ```TraceStep``` history, in order
    trace: list = field(default_factory=list)
    #: Every confirm decision of the run, in order
    confirmations: list = field(default_factory=list)
