"""
Configuration build-up and validation for the ai extension.

One place for everything that turns the raw ```ai``` configuration into the
shapes the handler runs, and for checking it. Each component kind has its own
module here -- ```guards```, ```tools```, ```sandboxes```, ```profiles```,
```providers``` -- holding the pure resolver for that kind (no app state, no
class loading; the handler passes in what it needs), so the handler and the
```linter``` draw from the same source instead of each walking the config on
its own.

The modules are imported directly where needed (no re-export proxy here), so a
consumer states which resolver it uses: ```from tokeo.core.ai.config.governors
import resolve_governors```. The ```linter``` (the ```ai lint``` command and the
pre-run check) lives here too; ```tokeo.core.ai.linter``` re-exports it for the
short path.

The full reference for the ```ai``` configuration section -- every notation,
the provider parameters, and using the loop from your own code -- is the
included guide below.

.. include:: ./CONFIG.md
"""
