# Security of Tokeo Fundi

Fundi lets a model call tools. Guards decide whether a call may run, a sandbox
is the wall it runs behind. A wall contains only what it is built for:

| Sandbox | Contains | Does not contain |
|---|---|---|
| ```in_process``` | nothing, the tool runs in the app | anything |
| ```subprocess``` | a crash, a hang (timeout), the environment (scrubbed) | file system, network, the user's rights |
| ```docker``` | file system (read-only, only the mounts), network (none), capabilities, memory, processes, time, the user (not root) | a flaw in the container runtime or the kernel |
| ```wasm``` | everything but the mounts: no network, no processes, memory, time, disk and file caps | a flaw in the WebAssembly runtime |

Model-generated code belongs in the ```wasm``` sandbox, never in
```in_process``` or ```subprocess```.

## Docker rights

> [!WARNING]
> Whoever may use the docker service is root on the host in effect: a user in
> the group ```docker``` can start a container that mounts ```/``` writable.

- Give the app docker rights only on a host where that is acceptable.
- Prefer rootless podman: ```command: podman``` in the sandbox options.
- Never mount the docker socket (```/var/run/docker.sock```) into a container
  of the app; a tool in it could start a container without the hardening.
- Keep the defaults: no network, read-only root, a user that is not root. A
  sandbox with more rights is a decision per tool, not a default.
