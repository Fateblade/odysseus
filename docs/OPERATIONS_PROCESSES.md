# Process ownership in Operations

The Host overview process table now joins each process's Docker cgroup ID with Docker's full container ID. It shows the matching container name and image in **Container / service**. Native processes retain their systemd service/scope.

For example, scope `docker-09e2b22b36fa…scope` belongs to container `09e2b22b36fa…`. In the reported inventory that is `odysseus-fateblade-odysseus-1`, image `odysseus-fateblade:dwarfstar-poc`. Its `uvicorn` process is Odysseus's Python web server; a process name need not match its container name or image.

Expand the process name to see its executable path and parent PID. Expand **Container ID and scope** for the full Docker ID and raw cgroup scope. Paths refer to the process executable as reported by Linux; Python applications commonly show the Python interpreter there. Full command arguments and environment variables are not collected because they can contain credentials.

Ownership uses cgroup identity, not guesses based on executable names. Docker inventory is sampled in parallel with processes, so a container created or removed during sampling may be unresolved until the next refresh. Restricted `/proc` access can make executable paths unavailable. If Docker inventory cannot be read, the ID remains visible with the container name marked unavailable. Both systemd Docker scopes and Docker cgroup directory paths are supported.

Deploy by pulling `feature/linux-ai-operations-dwarfstar` and rebuilding/recreating only Odysseus using your existing image tag. The existing SSH helper reads the updated host source. No additional host service, package installation or gateway recreation is needed for process ownership.
