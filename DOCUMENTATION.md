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
8. Exception to (7): continuation lines of a single colon-style note (the `:`
   format, see Notes) align under the note text — two spaces in, under the first
   character after `: ` — not the four-character code indent

### Section Headers

1. Structure sections with Markdown H3 headers: `### Section Name`
2. Never end a section header with a colon: write `### Args`, not `### Args:`.
   A trailing colon becomes part of the rendered heading and trips up pdoc.
3. Add a blank line after each section header before the bullets.
4. Do not show `None`
5. Common sections in this order:
   - `### Args` - Function parameters
   - `### Returns` - Return values
   - `### Raises` - Exceptions that may be raised
   - `### Notes` - Additional information
   - `### Reminders` - Important information mostly written in admonition blocks
   - `### References` - List with links or external content
   - `### Output` - Side effects (e.g., logging)
6. Add a blank line between each major section
7. If a section has no sensible content, do not show the section. Especially do not show when Args, Returns, or Raises is None.

### Argument Documentation

Format parameter documentation as bullet points:
- **parameter_name** (type): Description
- **optional_param** (type, optional): Description. Defaults to value.

For multiple types, use pipe separator:
- **param** (str|int): Description

For keyword arguments, document them with proper indentation:
- **kwargs**: Optional keyword arguments
    - **option1**: Description of option1
    - **option2**: Description of option2

### Return Values

Format return value documentation as bullet points:
- **type**: Description of the return value

For multiple possible return types:
- **type1|type2**: Description

### Exceptions

Document exceptions that may be raised:
- **ExceptionType**: Description of when/why this exception occurs

### Notes

Format the Notes section based on the number of items:

For multiple notes, use bullet points:
### Notes

- First note item with details
- Second note item with details
- Third note item with details

For a single note, use colon format with indentation:
### Notes

: Single note item with details that may span
  multiple lines with consistent indentation

Note the continuation line above: it aligns under the note text (two spaces),
which is the indentation exception called out in Basic Structure (8).

### Lists

Use `-` bullets for items with no inherent order — this is the default. Reserve
numbered lists (`1.`, `2.`, …) for genuine step-by-step sequences, and then
number them properly instead of repeating `1.`. The same rule applies to a
sub-list inside a single colon-style note.

### Reminders and Admonitions

Put warnings and important callouts under the `### Reminders` section as
RST-style admonition blocks. For example:

    ### Reminders

    .. warning::

        Disabling certificate verification exposes the connection to
        man-in-the-middle attacks. Use it only for local development.

Other admonition types follow the same form: `.. note::`, `.. tip::`,
`.. important::`, `.. caution::`. Leave a blank line after the directive and
indent the body by four spaces.

Do not place a horizontal rule (`---`) directly after an admonition or between
an admonition and the next section heading. The admonition box already closes
the block visually; a rule on top of it reads as a double divider. Let the next
heading follow after a blank line instead.

### Code Formatting

1. Use **bold** (double asterisks) for parameter names and return types: `**param_name**`
2. Use double backticks for inline code: `` ``code`` ``. They are required for
   dotted identifiers such as `` ``app.dramatiq.locks`` `` or `` ``ui.button`` ``:
   with single backticks pdoc tries to resolve them as cross-references and
   emits a ReferenceWarning when they cannot be linked.
3. Escape special characters with backslashes: `` `\_\_special\_\_` ``
4. For multiple return types or parameter types, use the pipe symbol: `(str|int)`

### Property Documentation

Document Python `@property` methods with the same style as functions, but focus on the value they provide rather than implementation details.

Key points for property docstrings:
- Include Returns section to document the type returned
- Don't include Args section for the property itself (only for property setters)
- Describe caching behavior if the property uses lazy loading
- Focus on explaining what the property represents

### Module Docstrings and Feature Lists

Module-level docstrings may include a `### Features` section listing what the
module provides. Write each item as a concrete, verb-led statement of behavior,
not a bold marketing label:

- Yes: `- Runs commands locally or remotely over SSH`
- No:  `- **Remote execution** via SSH or local shells`

Do not lead a bullet with a bold noun phrase; describe what the code does,
starting with a verb where possible.

### Linter Interactions

Docstrings are checked by the same lint pass as code, so a few interactions
matter:

1. **Triple backticks count toward W505.** flake8 with `--max-doc-length 84`
   counts the backtick characters of an inline `` ```identifier``` `` span. A
   docstring line with a long dotted path in backticks can exceed 84 even though
   the prose is short — rephrase to keep the long identifier out of the running
   text, or break the line.
2. **A docstring containing a regex needs an `r"""` prefix.** Backslash escapes
   in a normal `"""` docstring raise W605 / a SyntaxWarning (and the compile
   check runs with `-W error::SyntaxWarning`). Use a raw docstring `r"""…"""`
   whenever the text shows a regex or other backslashes.
3. **Template sources are not valid Python.** A file under
   `templates/generate/` contains `{{ }}` placeholders, so flake8/compile/pyink
   cannot read it as Python. Lint and format the GENERATED output instead, and
   exclude the template tree from pyink with
   `--force-exclude '/templates/generate/'`.

### Additional Best Practices

1. Keep descriptions clear and concise
2. Include format specifications when relevant (e.g., `Format: 'YYYY-MM-DD'`)
3. Document default values for optional parameters
4. For class methods, document the purpose, parameters, and return values in the same style
5. Use consistent indentation within docstring sections
6. For properties, focus on what they provide rather than implementation details

This ensures consistent, readable documentation that renders correctly in documentation tools.
