"""
Proxy for the ai configuration linter.

The linter lives in ```tokeo.core.ai.config.linter``` (with the rest of the
configuration build-up and validation); this re-exports it so the short path
```from tokeo.core.ai.linter import TokeoAiLinter``` keeps working.
"""

from tokeo.core.ai.config.linter import TokeoAiLinter, AiLintIssue

__all__ = [
    'TokeoAiLinter',
    'AiLintIssue',
]
