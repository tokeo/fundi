# Building a tool

A tool is a capability the model may call. It declares what it does and which
arguments it takes, and it runs that work in ```exec```. Everything else --
which sandbox contains it, whether an agent may reach it, how its result travels
back to the model -- the framework does around it.

```python
class ReadReport(TokeoAiTool):

    class Meta:
        description = 'read a quarterly report'
        parameters = {
            'type': 'object',
            'properties': {'quarter': {'type': 'string'}},
            'required': ['quarter'],
        }
        config_defaults = {}

    def exec(self, quarter):
        return f'report for {quarter}'
```

```exec``` receives the arguments unpacked, so Python itself raises when the
model omits a required one. Return a ```ToolResult``` for full control, or a
plain value the framework wraps for you.

## The wall around exec

```exec``` is the only part that runs inside the sandbox. The model's arguments
are what it processes, and that processing is what an ```in_process```,
```subprocess``` or ```wasm``` sandbox contains. A tool's own throw does not
escape: it comes back as a result carrying the exception, so the loop keeps
running and the model can react.

## The four hooks around it

A tool may implement four optional methods. In order:

```
prepare → before → exec → after → teardown
```

```prepare``` and ```teardown``` are the outer frame: open a resource, close it
again. ```before``` and ```after``` are the inner pair: reshape what goes in and
what comes out. All four run in-process, outside the sandbox, and receive the
run's ```turndata```, the call ```arguments``` and a ```track``` for a note on
the trace.

### prepare and teardown -- the frame

```python
def prepare(self, turndata, arguments, track):
    self.workdir = tempfile.mkdtemp()
    track(f'workdir {self.workdir}')

def teardown(self, turndata, arguments, result, track):
    shutil.rmtree(self.workdir, ignore_errors=True)
```

They return nothing and steer nothing. ```prepare``` can only stop the call by
raising. Its success is what arms ```teardown```: from then on ```teardown```
runs whatever happens -- and it is the one hook that always sees how the call
ended, which makes it the place to record an outcome.

### before and after -- a governor at the tool

```python
def before(self, turndata, arguments, track):
    track('path pinned to the safe root')
    return {**arguments, 'path': f'/safe/{arguments["path"]}'}

def after(self, turndata, arguments, result, track):
    return create_tool_result(f'{result.value} (from {arguments["path"]})')
```

```before``` returns the arguments to run with, ```after``` the result to hand
on; ```None``` from either leaves things as they were. ```after``` only runs
when the call succeeded -- there is no result to reshape otherwise. Both receive
the arguments **as reshaped**: whoever changes something, their version is what
everyone downstream sees.

## Data, not instruction

The four hooks sit outside the sandbox, which is exactly why one line must hold:
**reshape data, never carry out the model's instruction.** Sanitizing a path,
adding a flag, enriching a result -- all data work, all fine. Running what the
model asked for belongs in ```exec```, behind the wall. The test is simple: what
```before``` hands back is arguments, never an effect on the machine.

Two practical consequences. What ```before``` returns must stay JSON-able, like
all sandbox arguments, or the handoff to a subprocess or wasm sandbox fails. And
the hooks get ```turndata``` and ```arguments``` -- no context, no loopdata; the
loop's own counters are none of a tool's business.

## Delegating from before

```before``` may run a whole sub-turn through ```self.app.ai.chat```, for
instance to have a second model check the request before it executes:

```python
def before(self, turndata, arguments, track):
    verdict = self.app.ai.chat(
        [{'role': 'user', 'content': f'is this safe? {arguments}'}],
        turndata_preset=turndata,
    )
    track(f'checked: {verdict.answer.text[:40]}')
    return arguments
```

Pass ```turndata_preset=turndata``` and the sub-turn shares the caller's data
area. That is the recursion brake: a mark the outer run left is visible inside,
so a chain can stop itself -- without forcing the sub-turn's trace into the main
one. What surfaces is your choice: normally just the result, in a debug run the
whole sub-trace.

## When something raises

| raises | exec | after | teardown | the call's result |
|---|---|---|---|---|
| ```prepare``` | no | no | **no** | the exception |
| ```before``` | no | no | yes | the exception |
| ```exec``` | -- | no | yes | the exception |
| ```after``` | -- | -- | yes | the exception |
| ```teardown``` | -- | -- | -- | unchanged, the failure is noted |

One model throughout: a throw becomes a result carrying the exception, the rest
is skipped, and the call goes straight to ```teardown```. Only ```prepare``` is
the exception to the frame -- nothing was set up, so nothing is torn down.

A failing ```teardown``` never overturns a result the call already produced; it
is recorded on the trace and the good result stands. Machinery failures around
the tool -- a sandbox timeout, a denial -- are not tool errors and keep rising
as they always did.

## Tool hook or governor?

Both can reshape a call. The difference is what they are bound to.

Use a **hook** when the logic belongs to this one tool: its temp directory, its
argument sanitizing, its result format. It lives in the tool's class, needs no
config entry, and cannot be forgotten when the tool is used.

Use a **governor** when the policy spans tools: a budget, an allowlist, an
idempotence rule across several calls. It is declared in the config, sees every
call, and decides by name which ones it cares about.

Rule of thumb: if you would have to ask "is this call mine?", write a governor.
If the answer is always yes, write a hook.
