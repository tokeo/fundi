# Tokeo Fundi AI Documentation Guidelines

## Docstring Style

When writing docstrings in this project, follow these Markdown formatting conventions:

### Basic Structure

1. Use triple double quotes (\") for all docstrings
2. Start with a concise description of what the function/class does
3. For complex functions, add 1-2 additional lines explaining the behavior
4. Add a blank line between the description and the first section header
5. Use consistent capitalization and punctuation
6. Important! Break the lines by a maximum of 84 characters per line
7. If a line needs to be broken, indent the following lines by 4 chars like python code
8. Continuation lines of a note follow (7) in both formats, the colon-style
   ```:``` and the bullet ```-``` (see Notes): they are indented by 4 chars, so
   both formats line up

### Section Headers

1. Structure sections with Markdown H3 headers: ```### Section Name```
2. Never end a section header with a colon: write ```### Args```, not ```### Args:```.
   A trailing colon becomes part of the rendered heading and trips up pdoc.
3. Add a blank line after each section header before the bullets.
4. Do not show ```None```
5. Sections in this order:
   - ```### Args```: function parameters
   - ```### Keyword Args```: the single keys of ```**kwargs```
   - ```### Returns```: return values; for generators ```### Yields``` instead
   - ```### Raises```: exceptions that may be raised
   - ```### Example```: an example as code block
   - ```### Notes```: additional information
   - ```### Reminders```: warnings and important conditions as admonition blocks
   - ```### References```: list with links or external content
   - ```### Output```: side effects (e.g., logging)
6. The docstring of a class documents its constructor: the parameters of
   ```__init__``` go under ```### Args``` (and ```### Keyword Args```) of the
   class. ```__init__``` has no docstring of its own. The order in a class:
   Args, Keyword Args, Raises, Example, Notes, Reminders, References. Its
   attributes are documented by ```#:```, see Attributes.
7. Other section names are not used: no ```### Usage``` (use ```### Example```),
   no ```### See Also``` (use ```### References```), no ```### Methods```,
   since pdoc lists the methods itself, and no ```### Attributes``` (use ```#:```).
8. Add a blank line between each major section
9. If a section has no sensible content, do not show the section. Especially do not show when Args, Returns, or Raises is None.

### Argument Documentation

Format parameter documentation as bullet points:
- **parameter_name** (type): Description
- **optional_param** (type, optional): Description. Defaults to value.

For multiple types, use pipe separator:
- **param** (str|int): Description

Document the single keys of ```**kwargs``` in their own section:

    ### Keyword Args

    - **option1** (str): Description of option1
    - **option2** (int, optional): Description of option2. Defaults to 0.

### Return Values

Format return value documentation as bullet points:
- **type**: Description of the return value

For multiple possible return types:
- **type1|type2**: Description

### Exceptions

Document exceptions that may be raised:
- **ExceptionType**: Description of when/why this exception occurs

### Attributes

1. Values on module and class level, also those of ```Meta```, and instance
   attributes (```self.x = ...``` in ```__init__```) are documented by a
   ```#:``` comment right above the assignment. pdoc shows the text at the
   value:

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
   attributes in ```__init__``` only; for one set elsewhere see (5).
3. A private name (with a leading underscore) stays hidden. ```@public``` at
   the start of its ```#:``` shows it, ```@private``` hides a public one; the
   marker itself is not shown:

        #: @public Strong refs to scheduled log tasks
        self._pending_log_tasks = set()

4. A type may lead the text in parentheses, as in Args. pdoc shows it as the
   annotation of the value; it is optional:

        #: (str) Identifier of the executed task
        self.task_id = task_id

5. An instance attribute that ```__init__``` does not set with one assignment,
   because it is set in ```_setup``` or another method or only in the branches
   of an ```if```, is declared on class level with ```None``` and documented
   there. Code that asks whether such an attribute is set checks its value
   (```getattr(obj, 'x', None) is not None```), not ```hasattr```, which always
   finds the value of the class:

        #: (TokeoDiskCacheLocksHandler) Handler for locks and rate limiting, set on
        #: setup
        locks = None

### Notes

Choose the format by the kind of the notes, not by their number:

Use the colon format for separate hints. Each note stands on its own and is
shown as its own paragraph; a blank line separates the notes:

### Notes

: First hint that stands on its own and may span
    multiple lines with consistent indentation

: Second hint, independent of the first

Use bullet points for an enumeration, short points of the same kind that are
read together, e.g. the parts of an output or the steps of a setup; the
bullets follow each other without a blank line:

### Notes

- First point of the enumeration
- Second point of the enumeration
- Third point of the enumeration

A colon note may hold an enumeration of its own, indented by 4 chars. In both
formats continuation lines are indented by 4 chars (see Basic Structure 8).

### Lists

Use ```-``` bullets for items with no inherent order; this is the default.
Reserve numbered lists (```1.```, ```2.```, ...) for genuine step-by-step
sequences, and then number them properly instead of repeating ```1.```. The same
rule applies to a sub-list inside a single colon-style note.

### Reminders and Admonitions

Warnings and important conditions are put only under the ```### Reminders```
section as RST-style admonition blocks, never as ```**Important:**``` in the
Notes:

- ```.. warning::``` for security, e.g. a connection without checking the
  certificate
- ```.. important::``` for a condition whose disregard breaks a promise of the
  function, e.g. an ```expire``` shorter than the run time

For example:

    ### Reminders

    .. warning::

        Disabling certificate verification exposes the connection to
        man-in-the-middle attacks. Use it only for local development.

Other admonition types follow the same form: ```.. note::```, ```.. tip::```,
```.. caution::```. Always leave a blank line after the directive and indent the
body by four spaces.

Do not place a horizontal rule (```---```) directly after an admonition or
between an admonition and the next section heading. The admonition box already
closes the block visually; a rule on top of it reads as a double divider. Let
the next heading follow after a blank line instead.

### Code Formatting

1. Use **bold** (double asterisks) for parameter names and return types: ```**param_name**```
2. Use triple backticks for inline code: ```` ```code``` ````, e.g.
   ```` ```app.dramatiq.locks``` ````. The span may hold single backticks.
3. Escape special characters with backslashes: ```` ```\_\_special\_\_``` ````
4. For multiple return types or parameter types, use the pipe symbol: ```(str|int)```

### Property Documentation

Document Python ```@property``` methods with the same style as functions, but focus on the value they provide rather than implementation details.

Key points for property docstrings:
- Include Returns section to document the type returned
- Don't include Args section for the property itself (only for property setters)
- Describe caching behavior if the property uses lazy loading
- Focus on explaining what the property represents

### Module Docstrings and Feature Lists

Module-level docstrings may include a ```### Features``` section listing what the
module provides. Write each item as a concrete, verb-led statement of behavior,
not a bold marketing label:

- Yes: ```- Runs commands locally or remotely over SSH```
- No:  ```- **Remote execution** via SSH or local shells```

Do not lead a bullet with a bold noun phrase; describe what the code does,
starting with a verb where possible.

### Linter Interactions

Docstrings are checked by the same lint pass as code, so a few interactions
matter:

1. Triple backticks count toward W505. flake8 with ```--max-doc-length 84```
   counts the backtick characters of an inline ```` ```identifier``` ```` span. A
   docstring line with a long dotted path in backticks can exceed 84 even though
   the prose is short: rephrase to keep the long identifier out of the running
   text, or break the line.
2. A docstring containing a regex needs an ```r"""``` prefix. Backslash escapes
   in a normal ```"""``` docstring raise W605 or a SyntaxWarning (and the compile
   check runs with ```-W error::SyntaxWarning```). Use a raw docstring
   ```r"""..."""``` whenever the text shows a regex or other backslashes.
3. Template sources are not valid Python. A file under
   ```templates/generate/``` contains ```{{ }}``` placeholders, so
   flake8/compile/pyink cannot read it as Python. Lint and format the GENERATED
   output instead, and exclude the template tree from pyink with
   ```--force-exclude '/templates/generate/'```.

### Additional Best Practices

1. Keep descriptions clear and concise
2. Include format specifications when relevant (e.g., ```Format: 'YYYY-MM-DD'```)
3. Document default values for optional parameters
4. For class methods, document the purpose, parameters, and return values in the same style
5. Use consistent indentation within docstring sections
6. For properties, focus on what they provide rather than implementation details

This ensures consistent, readable documentation that renders correctly in documentation tools.
