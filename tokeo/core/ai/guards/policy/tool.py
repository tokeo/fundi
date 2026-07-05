"""
A ready-to-use policy guard that allows or denies a tool call by its name.

```TokeoAiToolPolicyGuard``` is the action-level governance baseline: it shapes
what an agent may *do*, not just what it may say. Rules come from the guard
entry's ```options``` (```allow``` and ```deny``` lists). A denied call is not
executed; the loop continues and the model sees a ```denied: ...``` result, so
the agent can react instead of crash (deny-and-continue).

```yaml
ai:
  guards:
    safe:
      type: tool_policy
      options:
        deny: [shell]
    mathonly:
      type: tool_policy
      options:
        allow: [calc]
```
"""

from tokeo.core.ai import Invocation
from tokeo.core.ai.governor import GOVERNOR_STAGE_ON_CALL
from tokeo.core.ai.guards.policy.base import TokeoAiPolicyGuard


class TokeoAiToolPolicyGuard(TokeoAiPolicyGuard):
    """
    An ```on_call``` policy guard that permits or blocks tool calls by name.

    The rules are read from ```_meta``` (set from the guard entry's
    ```options```): ```deny``` is a denylist and always wins; ```allow```, when set,
    is an allowlist that restricts calls to its members. With neither rule the
    guard permits every call (it then only documents intent). A denial is soft:
    ```decision``` is set to ```deny``` and the loop continues.

    """

    class Meta:
        """Policy rules, overridden per guard by its entry's options."""

        # the configurable defaults, as one dict; a guard entry's options (and an
        # on_call override) overlay this, read at runtime via _config. allow:
        # tools allowed, None means "allow any tool not denied". deny: tools
        # always denied, a deny wins over the allowlist
        config_defaults = dict(
            allow=None,
            deny=[],
        )

    def on_call(self, ctx, invocation):
        """
        Deny the call when the policy forbids the tool; otherwise allow.

        Runs at the tool-call stage, before exec, so it can stop a call from
        running. ```ctx``` is the running state (unused here).

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The tool call to check; on a denial its
            ```decision``` is set to ```deny``` with a ```reason```

        """
        config = self._config(GOVERNOR_STAGE_ON_CALL)
        name = invocation.name
        denied = name in (config.get('deny') or [])
        if not denied and config.get('allow') is not None:
            denied = name not in config.get('allow')
        if denied:
            invocation.decision = Invocation.DENY
            invocation.reason = f'tool {name!r} is not permitted by policy'
