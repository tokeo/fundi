"""
The run context: the manager of one agent-loop run's history.

```TokeoAiContext``` is the active manager that travels a run -- it holds the one
chronological trace of every object the run produces and keeps the typed views
over it consistent. It lives apart from ```data.py``` (the passive value shapes)
because it carries behaviour (```track```, ```tracked```, the typed-view
properties), not just fields.
"""

from tokeo.core.ai.data import ChatMessage, Invocation, ChatResult, TraceStep, TokeoAiLoopdata, TokeoAiTurndata
from tokeo.core.ai.exc import TokeoAiError


class TokeoAiContext:
    """
    The state that travels one agent-loop run, and its manager.

    A run is one pass through the loop in ```chat``` (whether entered through
    ```chat``` or the ```ask``` facade). The context is the manager of the run's
    history: it holds one chronological trace of steps and keeps typed
    quick-access caches of the bare objects, because only it writes them.

    The trace is the run's history -- a list of ```TraceStep``` (an origin and
    the object it left in hand), in order. ```track``` adds a step for a fresh
    object (the loop is the origin); ```supersede``` adds a step for each guard
    that ran (the guard is the origin), so the trace shows *who* acted and *what*
    the object looked like after, step by step -- the record for audit and
    analysis.

    The caches are a different thing: ```messages```, ```invocations```,
    ```results``` are lists of the *bare objects* in their current state, grouped
    by kind, so a guard can iterate one kind without walking the trace. They hold
    objects, not steps. ```tracked``` returns a cache by type; ```cur_invocation```
    is the latest tool call. ```track``` appends the bare object to each cache it
    matches; ```supersede``` swaps the last cache entry of a kind when a guard
    hands back a fresh object (so a cache stays one entry per tool call, while the
    trace keeps every step).

    The context carries only what accumulates -- the trace, the caches, and
    ```status``` (the counters). The current single objects (the round's result,
    a call's invocation) are not fields here: the loop tracks them and hands them
    to the stage that needs them, and ```cur_invocation``` reaches the latest from
    the cache. Apart from these it holds ```userdata```: an opaque value the
    caller may set once and the framework never touches, carried unchanged
    through the run (not history, not a counter -- a constant the caller can read
    its own context back from). It also holds ```turndata```: a free, shared
    dict that lives one run for in-process participants to keep working state
    under their own key.

    The cached kinds are fixed at construction (```ChatMessage```,
    ```Invocation```, ```ChatResult```), so a cache exists for each from the
    start; adding a kind is naming one more class.

    """

    # the kinds that get a typed view; track files an object into each one it is
    # an instance of (isinstance, so a subclass lands in its base's view too)
    _CACHED = (ChatMessage, Invocation, ChatResult)

    def __init__(self, messages=None, turndata_preset=None, userdata=None, trace=True):
        """
        Start a run's context, optionally seeding the incoming messages.

        ### Args

        - **messages** (list | None): The incoming conversation to seed the run
            with; each is tracked, so it is on the trace and in the messages view
        - **turndata_preset** (dict | None): Starting values for this run's
            ```turndata```, taken 1:1 into a fresh ```TokeoAiTurndata``` (NOT
            deep-copied, so nested objects are shared -- copy first to protect
            them). Lets a delegate share its caller's turndata. Defaults to
            ```None``` (empty)
        - **userdata**: An opaque value the caller may carry through the run. The
            framework never reads, interprets, or changes it -- it only makes it
            available on the context, so a guard (or a later confirm hook) can
            find the caller's own context by it. Its content is the caller's
            concern (a string, an id, a struct); the only guarantee is that it
            arrives unchanged. Defaults to ```None```
        - **trace** (bool): Whether to record the step history. ```True``` (the
            default) builds the full trace; ```False``` skips appending steps
            while the typed caches (messages, invocations, results) still fill
            normally, so guards keep working. Only safe when no active guard
            reads the trace itself -- guards read the caches, not the history

        """
        # whether to record the step history; the caches always fill
        self._record_trace = trace
        # the one chronological stream of every tracked object
        self._trace = []
        # the typed views, one list per cached kind, by exact class
        self._caches = {kind: [] for kind in self._CACHED}
        # the loop's counters
        self.loopdata = TokeoAiLoopdata()
        # a free, shared data area for this run; participants write under
        # their own key; a preset seeds it (see __init__ args)
        self.turndata = TokeoAiTurndata(turndata_preset) if turndata_preset is not None else TokeoAiTurndata()
        # the caller's opaque carry-through value; set once, never touched by
        # the framework, constant for the run (not history, not a counter)
        self.userdata = userdata
        # seed the incoming conversation, as ChatMessage so it is typed in the
        # cache; a plain dict is wrapped, an existing ChatMessage kept as is. the
        # context itself is the origin of these seed steps (the incoming request,
        # before any loop turn or guard)
        for message in messages or []:
            self.track(self, message if isinstance(message, ChatMessage) else ChatMessage(message))

    def _append_step(self, origin, obj, changed, stage):
        """
        Append one TraceStep -- the shared trace-write of track/supersede/refine.

        The trace half that every recorder has in common: build the step (origin,
        object, changed, stage) and append it. The cache half differs per
        recorder and stays in each.

        ### Args

        - **origin**: Who produced the step (the loop or a guard)
        - **obj**: The object as it stood after the step
        - **changed** (bool): Whether a new object was introduced
        - **stage**: The guard stage, or None for a loop track

        """
        # skip when the run does not record its history; the caches still fill
        # in track/supersede/refine_messages, so guards keep working
        if not self._record_trace:
            return
        self._trace.append(TraceStep(origin=origin, object=obj, changed=changed, stage=stage))

    def track(self, origin, obj, stage=None):
        """
        Record a fresh object: a step on the trace, the object in its cache.

        The adder for a *new* object entering the run (a message, a model
        result, a freshly built invocation). It appends a ```TraceStep``` to the
        trace (the history) and the bare object to each cache it matches (the
        state by kind). The trace carries steps; the caches carry bare objects --
        the two are kept in step here, because only ```track```,
        ```supersede``` and ```refine_messages``` write them.

        ### Args

        - **origin**: Who creates this object -- the loop (its handler) for a
            fresh message/result/invocation. Recorded on the step so the history
            is attributable
        - **obj**: The run object to record (a ```ChatMessage```,
            ```Invocation```, ```ChatResult```, or any future cached kind)
        - **stage**: The guard stage, or ```None``` for a loop track outside any
            stage (the common case here -- the loop adds fresh objects)

        ### Returns

        - The same object, so a caller can track and keep the reference in one
            step

        """
        # the trace gets a step (a fresh object is always a "changed" step); the
        # caches get the bare object, filed into every kind it is an instance of
        self._append_step(origin, obj, True, stage)
        for kind, bucket in self._caches.items():
            if isinstance(obj, kind):
                bucket.append(obj)
        return obj

    def supersede(self, origin, returned, current, stage=None):
        """
        Record a guard's step: always a trace step, a cache swap only if new.

        Called by the loop after each guard at a single-object refining stage
        (```on_answer```/```on_call```/```on_return```/```on_close```), with
        whatever the guard returned and the object the guard was handed. A step
        is *always* appended (every guard that ran is on the trace, attributable).
        Whether the cache changes depends on identity:

        - ```returned``` is ```None``` or the *same* object as ```current``` --
            the guard added no new object (it mutated in place, or did nothing).
            The cache is left alone; the step records ```current``` with
            ```changed=False```. The current working reference is unchanged.
        - ```returned``` is a *different* object -- the guard handed back a fresh
            copy. It supersedes the last cache entry of its kind, and the step
            records it with ```changed=True```. The returned object becomes the
            new working reference.

        A guard whose return is neither ```None``` nor the same nor the same
        *kind* as ```current``` is a bug (an ```on_return``` guard must return an
        ```Invocation```), so this raises rather than silently mis-filing it.
        The whole-messages list is a different shape and has its own recorder
        (```refine_messages```); this one is for the single cached objects.

        ### Args

        - **origin**: The guard that ran (recorded on the step)
        - **returned**: What the guard returned (a fresh object, the same object,
            or ```None```)
        - **current**: The object the guard was handed (the current working
            reference for its kind)
        - **stage**: The guard stage that ran (recorded on the step)

        ### Returns

        - The working reference to carry on with: ```returned``` when it is a new
            object, otherwise ```current```

        """
        if returned is None or returned is current:
            # the guard added no new object: a step that holds the existing
            # reference, the cache untouched
            self._append_step(origin, current, False, stage)
            return current
        # a new object must be of the same kind, or the guard returned something
        # it should not have -- fail loud, do not mis-file it
        if type(returned) is not type(current):
            raise TokeoAiError(f'guard {origin!r} returned a {type(returned).__name__}, expected a {type(current).__name__}')
        # the new object supersedes the last cache entry of its kind; the step
        # records the transition with the guard as its origin
        self._append_step(origin, returned, True, stage)
        for kind, bucket in self._caches.items():
            if isinstance(returned, kind) and bucket and bucket[-1] is current:
                bucket[-1] = returned
        return returned

    def refine_messages(self, origin, returned, stage=None):
        """
        Record a pre-model guard's step on the whole conversation.

        Called by the loop after each ```on_begin``` / ```on_prompt``` guard.
        Those stages act on the accumulated conversation, not a single handed-in
        object, so this is the messages counterpart of ```supersede```: the unit
        here is the whole ```messages``` list, not one cache entry.

        - ```returned``` is ```None``` or *is* the live ```messages``` list -- the
            guard refined in place (or did nothing). The cache is left alone; the
            step records a snapshot of the list with ```changed=False```.
        - ```returned``` is a *new* list -- the guard built a fresh conversation
            (a trimmed history, an injected system turn). Its items must all be
            mappings (a ```ChatMessage``` is a ```dict```), or it is a guard bug
            and this raises. The whole ```ChatMessage``` cache is replaced by the
            new turns (wrapped as ```ChatMessage```), in place so existing
            references to the live list stay valid; the step records a snapshot
            with ```changed=True```.

        The step holds a shallow copy of the list, not the live cache, so it
        shows the conversation as it stood at this stage -- the live list keeps
        growing through the run and would otherwise show the end state on every
        pre-model step.

        It is a separate method from ```supersede``` on purpose: ```supersede```
        replaces ONE cache entry (the last object of a kind); this replaces the
        WHOLE list. Different operation, different verb.

        ### Args

        - **origin**: The guard that ran (recorded on the step)
        - **returned**: What the guard returned (a new list, the same list, or
            ```None```)
        - **stage**: The guard stage that ran (recorded on the step)

        ### Returns

        - The live ```messages``` list to carry on with

        """
        cache = self._caches[ChatMessage]
        if returned is None or returned is cache:
            # the guard refined in place (or did nothing): a step holding a
            # SNAPSHOT of the conversation at this stage, the cache untouched.
            # a shallow copy of the list, since the cache list keeps growing as
            # the run goes on -- the live list would later show the end state
            self._append_step(origin, list(cache), False, stage)
            return cache
        # a returned conversation must be a list of mappings (ChatMessage is a
        # dict); anything else is a guard bug -- fail loud, do not corrupt the
        # cache
        if not isinstance(returned, list) or not all(isinstance(m, dict) for m in returned):
            raise TokeoAiError(f'guard {origin!r} returned an invalid messages list (expected a list of messages)')
        # replace the WHOLE conversation in place, so references to the live
        # list stay valid; wrap a plain dict as ChatMessage so the cache stays
        # typed (the same rule the constructor's seeding uses)
        cache[:] = [m if isinstance(m, ChatMessage) else ChatMessage(m) for m in returned]
        # a snapshot for the step, so it shows this stage's state, not the end
        self._append_step(origin, list(cache), True, stage)
        return cache

    def tracked(self, kind):
        """
        The typed view for a kind -- the bare objects of that type, in order.

        Returns the live cache list (not a copy), so it reflects later
        recording; read and iterate it, do not mutate it. An unknown kind yields
        an empty list, so a caller can iterate without guarding for absence.
        These are the objects in their current state, not trace steps.

        ### Args

        - **kind** (type): The class whose objects to return

        ### Returns

        - **list**: The objects of that kind, in creation order

        """
        return self._caches.get(kind, [])

    @property
    def trace(self):
        """The full chronological trace of every tracked object, in order."""
        return self._trace

    @property
    def messages(self):
        """The ```ChatMessage``` turns, in order -- the conversation history."""
        return self._caches[ChatMessage]

    @property
    def invocations(self):
        """The ```Invocation``` tool calls, in order."""
        return self._caches[Invocation]

    @property
    def results(self):
        """The ```ChatResult``` model answers, in order."""
        return self._caches[ChatResult]

    @property
    def cur_invocation(self):
        """
        The current tool call -- the latest ```Invocation``` in its newest state.

        A convenience over ```invocations[-1]``` (the cache, not the trace), so a
        guard or the loop can reach the call in hand without indexing. ```None```
        when no tool call has been made yet.
        """
        invocations = self._caches[Invocation]
        return invocations[-1] if invocations else None
