# Contributing to Tokeo-Fundi

Thank you for considering a contribution. Issues, ideas, and pull requests are
welcome. Please read this page before you open one.

<br/>

## How this project is licensed

Tokeo-Fundi is source-available under the **Tokeo-Fundi Source-Available
License 1.0**, see ```LICENSE.md```. Use is free of charge for private purposes
and for organisations within the limits stated there. Larger organisations
obtain a commercial Enterprise License from the maintainer.

That second part is what keeps the project funded and maintained. It only works
if the maintainer holds the rights to the whole codebase. Contributions
therefore come with the terms below.

<br/>

## Contribution terms

By opening a pull request or otherwise submitting material to this project, you
agree to the following for every contribution you make.

**You keep your copyright.** Nothing here transfers ownership of your work.

**You grant a broad license.** You grant the maintainer, Tom (Thomas)
Freudenberg, a worldwide, perpetual, irrevocable, royalty-free, non-exclusive
right to use, reproduce, modify, distribute, and sublicense your contribution,
and to relicense it under any terms, including the Tokeo-Fundi Source-Available
License, a commercial Enterprise License, or any future license of this
project. You also grant a patent license of the same scope for patent claims
you can license that your contribution would infringe.

**You confirm you may do this.** You confirm that the contribution is your own
work, or that you have the rights to submit it under these terms, and that it
does not knowingly infringe anyone else's rights. If your employer holds rights
in your work, you confirm that you have permission to contribute.

**No warranty from you.** Your contribution is provided as is. You are not
liable for it.

If you cannot agree to these terms, please open an issue describing the change
instead of a pull request. Ideas are just as valuable, and describing a problem
well is often the larger part of solving it.

<br/>

## What we accept

**Keep the human in the loop.** We use AI as an exoskeleton, not a replacement.
Purely AI-generated issues or pull requests are not accepted. If AI helped you
write code or text, that is fine. You must have read it, understood it, and be
able to explain and defend it.

**Small and focused beats large and sweeping.** One concern per pull request.
A change that touches contracts, guards, or sandboxes should say in the
description what it changes about the guarantees the runtime makes.

**Tests belong to the change.** New behaviour comes with tests. Changed
behaviour comes with changed tests. The mock provider exists so that the whole
pipeline is testable without any external service.

**Documentation belongs to the change.** If a change affects how the runtime is
configured or used, update the relevant document in the same pull request.

<br/>

## Before you open a pull request

```bash
# Format the code
make fmt

# Run linting checks
make lint

# Run the test suite
make test
```

A pull request that does not pass ```make lint``` and ```make test``` will be
asked to do so before review.

<br/>

## Reporting security issues

Please do not open a public issue for a security problem. Write to
th.freudenberg@gmail.com with a description and, if possible, a way to
reproduce it. You will get an answer.

<br/>

## Questions

If something here is unclear, or the terms above do not fit your situation, ask
before you invest work. An issue or an email is enough.
