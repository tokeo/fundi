# Tokeo Fundi AI Documentation Guidelines

How the code of this package is documented. The docs site is built from it with
pdoc and the tokeo theme (```make doc```).

## What to document

1. The package documents its public interface completely: every module, class,
   function, type and value it hands out gets a docstring or a ```#:``` comment.
   Internals get a comment only where the code does not show the reason.
2. Documentation says what the code does and what a user must know, not how
   the code works in detail. It addresses developers and users; it is never a
   memo or a record of agreements.
3. Code, documentation and configuration hold no references to chats,
   agreements, tickets or project documents. A statement carries itself.
4. Limits and traps are stated explicitly, e.g. "temper never stops a running
   call".
5. Configuration templates get short hints, not a book.
6. Diagrams are inline mermaid code blocks, never image files of their own.

## Module Docstrings

Every module starts with a docstring: a title after the pattern
```<Package> <Topic> Module.```, a short description, and if needed the sections
```### Features```, ```### Example``` and ```### Notes```, in this order:

````python
"""
Tokeo Time Limit Module.

Limits the run time of a function by the decorator ```@timelimit```.

### Example

```python
@timelimit(60)
def job():
    ...
```

### Notes

: A coroutine stops only at an ```await```

"""
````

An item under ```### Features``` is a concrete statement of behavior and starts
with a verb, not a bold label:

- Yes: ```- Runs commands locally or remotely over SSH```
- No: ```- **Remote execution** via SSH or local shells```

The text of a folder is the docstring of its ```__init__.py```, in a namespace
package an ```__init__.md```. A guide next to the code (e.g. ```WASM.md```) is
embedded there with ```.. include:: ./FILE.md```.

## Docstring Style

### Basic Structure

1. Use triple double quotes for all docstrings.
2. Start with a one-line summary that begins with a verb:
   ```Acquire a lock ...```, not ```This function ...```. Add one or two
   sentences on the behavior if needed.
3. Add a blank line between the description and the first section header.
4. Break the lines at 84 characters at most.
5. Indent continuation lines by 4 characters, in entries and in both formats of
   the notes, so they line up.

```python
def acquire(self, key, expire=3600):
    """
    Acquire a lock with the given key, waits until the lock is free.

    ### Args

    - **key** (str): The lock key to acquire
    - **expire** (float, optional): Seconds after which the lock expires,
        also when its holder died without release. Defaults to 3600.

    ### Returns

    - **bool**: True once the lock is acquired

    ### Raises

    - **ValueError**: If expire is not a number above 0

    ### Notes

    : The lock releases only itself

    """
```

### Section Headers

1. Write sections as ```### Name```, never with a colon at the end: a trailing
   colon becomes part of the rendered heading.
2. Add a blank line after each header and between the sections.
3. A section without content is left out; ```None``` is not shown.
4. These sections exist, in this order:
   - ```### Args```: the parameters
   - ```### Keyword Args```: the single keys of ```**kwargs```
   - ```### Returns```: the return value, for generators ```### Yields```
     instead
   - ```### Raises```: the exceptions
   - ```### Example```: an example as code block
   - ```### Notes```: further hints
   - ```### Reminders```: admonition blocks
   - ```### References```: links and sources
   - ```### Output```: side effects such as log output
5. Other names are not used: no ```### Usage``` (use ```### Example```), no
   ```### See Also``` (use ```### References```), no ```### Methods```, since
   pdoc lists the methods itself, and no ```### Attributes``` (use ```#:```).
6. The docstring of a class documents its constructor: the parameters of
   ```__init__``` go under ```### Args``` (and ```### Keyword Args```) of the
   class. ```__init__``` has no docstring of its own. The order in a class:
   Args, Keyword Args, Raises, Example, Notes, Reminders, References.

### Entries

- **parameter** (type): Description
- **optional_param** (type, optional): Description. Defaults to value.
- **param** (str|int): Several types are separated by ```|```.

The same form holds for ```### Keyword Args```. ```### Returns``` names only
the type: ```- **bool**: True once the lock is acquired```. ```### Raises```
names the exception: ```- **ValueError**: If expire is not a number above 0```.

### Notes

Choose the format by the kind of the content, not by the number of notes:

- ```: Text``` for separate hints. Each stands on its own and becomes a
  paragraph of its own; a blank line separates them.
- ```- Text``` for an enumeration of short points of the same kind that are
  read together. They follow each other without a blank line.

A colon note may hold an enumeration of its own, indented by 4 characters.

### Lists

Use ```-``` bullets for items with no inherent order; this is the default.
Numbered lists (```1.```, ```2.```, ...) are for genuine sequences of steps, and
then numbered properly.

### Reminders and Admonitions

Warnings and important conditions stand only under ```### Reminders```, never
as ```**Important:**``` in the notes:

- ```.. warning::``` for security, e.g. a connection without checking the
  certificate
- ```.. important::``` for a condition whose disregard breaks a promise of the
  function, e.g. an ```expire``` shorter than the run time

An admonition is a line ```.. <kind>::```, a blank line, and the text indented
by four spaces. The kinds are ```note```, ```tip```, ```important```,
```warning``` and ```caution```, and also ```info```, ```hint```, ```success```,
```danger```, ```error``` and ```deprecated```. The same form holds in the
markdown files. The docs site shows them as coloured boxes:

```
### Reminders

.. important::

    expire must be larger than the longest run time of the wrapped
    function, otherwise the limit breaks.
```

Do not place a horizontal rule (```---```) directly after an admonition; let the
next heading follow after a blank line.

### Code in Text

1. Code in running text stands in triple backticks: ```` ```app.cache.locks``` ````.
   This holds for docstrings and for the markdown files of the package. The span
   may hold single backticks.
2. Parameter names and types in entries are **bold**: ```**param_name**```.
3. Escape special characters with backslashes: ```` ```\_\_special\_\_``` ````.

### Properties

A property describes the value it provides. It has ```### Returns``` and no
```### Args``` (only a setter has Args). Mention caching if the property loads
lazily.

## Attributes and Values

1. Values on module and class level, also those of ```Meta```, and instance
   attributes (```self.x = ...``` in ```__init__```) are documented by a ```#:```
   comment right above the assignment. pdoc shows the text at the value. Each
   key of ```Meta.config_defaults``` has a comment above it:

        #: Dict with initial settings
        config_defaults = dict(
            # Default timeout for cache operations in seconds
            timeout=60,
        )

        def __init__(self, app):
            #: The Cement application instance
            self.app = app

2. An instance attribute appears in the documentation only with its ```#:```;
   without one it stays hidden, even with a public name. pdoc finds instance
   attributes in ```__init__``` only.
3. A private name (with a leading underscore) stays hidden. ```@public``` at the
   start of its ```#:``` shows it, ```@private``` hides a public one; the marker
   itself is not shown:

        #: @public Strong refs to scheduled log tasks
        self._pending_log_tasks = set()

4. A type may lead the text in parentheses, as in Args. pdoc shows it as the
   annotation of the value:

        #: (str) Identifier of the executed task
        self.task_id = task_id

5. An instance attribute that ```__init__``` does not set with one assignment,
   because it is set in ```_setup``` or another method or only in the branches
   of an ```if```, is declared on class level with ```None``` and documented
   there. Code that asks whether it is set checks its value
   (```getattr(obj, 'x', None) is not None```), not ```hasattr```, which always
   finds the value of the class:

        #: (TokeoDiskCacheLocksHandler) Handler for locks and rate limiting, set
        #: on setup
        locks = None

## Comments

1. A comment never retells the line below it. It explains the intent or the
   reason the code does not show.
2. Simple code gets no comment: imports, assignments, mappings, configuration
   that reads as it is written.
3. A flow of several steps gets a short lowercase comment before each step, also
   for short steps. Read together, the comments tell the flow.
4. A comment says a thing once, at the place it belongs. Other places refer to
   it briefly (```# see Context.turndata```).
5. A place that is dangerous to touch wrongly gets a hint with ```Attention!```.
6. A separator line splits a file or a large class into sections. It starts
   with four dashes, then the name of the section, then dashes to fill the line:
   ```# ---- locks ----...```.
7. Comments are ASCII and at most 84 characters per line.

## Type Annotations

Do not use type annotations. They stay only where the code needs them at
runtime (FastAPI parameters, pydantic models, dataclasses). All others are
removed, also in code taken over from other projects, until the framework is
moved to annotations completely.

## Linter Interactions

1. Triple backticks count toward W505: flake8 with ```--max-doc-length 84```
   counts the backtick characters of an inline span. Keep a long dotted path out
   of the running text, or break the line.
2. A docstring with a regex or other backslashes needs the prefix ```r"""```,
   otherwise flake8 reports W605 or Python a SyntaxWarning.
3. Templates with ```{{ }}``` placeholders are not valid Python. Lint and format
   the generated output instead, and exclude the templates from pyink.
