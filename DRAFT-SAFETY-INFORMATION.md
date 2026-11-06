# Safety information (draft)

The threat model of tokeo-fundi: whom fundi trusts, whom it does not, what it
does not protect against, and the principles that follow. This is a draft; its
final place is decided before the release.

## Trusted

- **The application and its developer.** Fundi runs inside the app. Whoever
  writes the app, its config and its tools can do anything the process can.
- **The configuration** (```ai.yaml``` and the environment files). It is the one
  place that says what an agent may do; the linter checks its shape at every
  start.
- **The tools of the application.** A tool does what its code does. The sandbox
  limits where it runs, not what it means to do.
- **The channels of a confirm.** A channel registered on the hook
  ```tokeo_ai_confirm``` speaks for a person (```by: human```) or for the app
  (```by: auto```); fundi records which, it cannot check it.

## Not trusted

- **The model.** Its answers are plans: tool calls with arguments. Every call
  passes the governors before it runs and the sandbox while it runs; arguments
  that are not a JSON object are refused before any guard.
- **Content that reaches the model**: tool results, files, documents of a
  retrieval, requests from outside. It may carry instructions (prompt
  injection).
- **Code the model writes.** It runs only in the wasm sandbox, with no network,
  explicit mounts and hard limits on memory, time, disk and files.
- **What a sandbox guest reports about itself.** In the wasm sandbox the guest
  only delivers its value; the host records the run states itself (exit code,
  trap, timeout, stdout and stderr through its own capture).

## What fundi enforces

- **Which tools exist for a run:** only those the agent carries, minus every
  ```deny``` of agent, profile and call. Nothing widens the set.
- **Where a tool runs:** in the first sandbox of the agent's chain that names
  it; without one the call is denied. There is no catch-all.
- **Whether a call runs:** a no of any layer wins. A confirm turns a yes into a
  no, never a no into a yes.
- **What a sandbox promises:** only what the platform enforces; a limit that
  cannot be enforced fails the call instead of running unlimited.
- **What is recorded:** every step of every governor, with its author, on the
  trace of the result.

## Not protected

- **A hostile developer** of the app, or a hostile config.
- **Crashes in tools that run ```in_process```**: there is no isolation there.
  ```subprocess``` separates crashes and resources, it is no jail: the tool can
  read files and reach the network.
- **Prompt injection steering the plan.** Injected text can change which of
  the permitted calls the model asks for and with which arguments. It cannot
  add a tool, lift a deny or leave a sandbox. Guards (```tool_policy```,
  ```tool_schema_validate```, ```regex_redact```, a confirm) narrow what such a
  plan can reach; they do not detect every injection.
- **Side channels**, timing, and the resources of the host outside the limits a
  sandbox sets.
- **The quality of an answer.** Fundi governs what runs, not whether the
  model's text is right.
- **Secrets in places redact does not see.** ```regex_redact``` masks at the
  tool stages and in the stored tool call; it matches patterns, it cannot find
  every secret.
- **Docker rights.** Whoever may use the docker service is root on the host in
  effect. The docker sandbox needs that right; never mount the docker socket
  into a container of the app.

## Principles

1. **The model plans, the pipeline governs, the tools compute.** No step is
   implicit; every step is on the trace.
2. **A no always wins, and an agent can never do more than its config
   allows.** Every layer can only narrow.
3. **A confirm is decided by a person or by the application, never by the model
   of the run.** The answer records ```by: human``` or ```by: auto```; a function
   of the app may consult another model as part of its own chain.
4. **A governor decides, a sandbox contains.** No governor runs inside a
   sandbox, and no sandbox decides.
5. **We promise only what can be enforced.**
