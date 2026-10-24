"""Server-configured local or SSH transport to the Operations host helper."""

import asyncio
import json
import os
import re
import shlex
import sys
from pathlib import Path

from services.operations.litellm_config import MAX_CONFIG_BYTES, OperationsError


class HostOperations:
    def __init__(self, environ=None):
        env = os.environ if environ is None else environ
        self.config_path = env.get("ODYSSEUS_LITELLM_CONFIG_PATH", "")
        self.container = env.get("ODYSSEUS_LITELLM_CONTAINER", "litellm")
        self.base_url = env.get("ODYSSEUS_LITELLM_BASE_URL", "http://127.0.0.1:4000")
        self.remote = env.get("ODYSSEUS_OPERATIONS_SSH_HOST", "")
        self.port = env.get("ODYSSEUS_OPERATIONS_SSH_PORT", "22")
        self.python = env.get("ODYSSEUS_OPERATIONS_HOST_PYTHON", "python3")
        self.helper = env.get("ODYSSEUS_OPERATIONS_HOST_HELPER", "")
        self.strata_url = env.get("ODYSSEUS_OPERATIONS_STRATA_URL", "http://127.0.0.1:8081")
        self.ollama_url = env.get("ODYSSEUS_OPERATIONS_OLLAMA_URL", "http://127.0.0.1:11434")
        self.dwarfstar_url = env.get("ODYSSEUS_OPERATIONS_DWARFSTAR_URL", "")
        self.maya_url = env.get("ODYSSEUS_OPERATIONS_MAYA_URL", "")
        self.physical_memory_gib = env.get("ODYSSEUS_OPERATIONS_PHYSICAL_MEMORY_GIB", "0")
        self.management_path = env.get("ODYSSEUS_OPERATIONS_MANAGEMENT_CONFIG", "")

    async def run(self, action, values=None):
        if action not in ("snapshot", "preview", "probe", "apply", "overview", "resources") and not self.remote and not self.helper:
            from src.host_docker_access import running_in_container
            if sys.platform != "linux" or running_in_container():
                if action in ("management", "residency", "jobs", "strata_config", "dwarfstar_config", "maya_config", "audit"):
                    return {"configured": False, "message": "Connect the Linux host helper to enable management and jobs."}
                raise OperationsError("Linux host access is not configured.", 503)
        if not self.config_path and action in ("snapshot", "preview", "probe", "apply"):
            if action == "snapshot":
                return {"configured": False, "message": "Host access is not configured. Connect the Operations host helper to read the host and manage LiteLLM."}
            raise OperationsError("LiteLLM host access is not configured.", 503)
        if action in ("overview", "resources") and not self.remote and not self.helper:
            from src.host_docker_access import running_in_container
            if sys.platform != "linux" or running_in_container():
                return {"configured": False, "message": "Connect the helper on the Linux host to read its overview."}
        payload = {**(values or {}), "action": action}
        data = json.dumps(payload).encode("utf-8")
        if len(data) > MAX_CONFIG_BYTES:
            raise OperationsError("The operation request exceeds the 1 MiB limit.", 413)
        if not self.physical_memory_gib.isdigit() or not 0 <= int(self.physical_memory_gib) <= 4096:
            raise OperationsError("Configured physical memory must be between 0 and 4096 GiB.", 503)
        args = ["--config", self.config_path, "--container", self.container, "--base-url", self.base_url,
                "--strata-url", self.strata_url, "--ollama-url", self.ollama_url, "--dwarfstar-url", self.dwarfstar_url,
                "--maya-url", self.maya_url, "--physical-memory-gib", self.physical_memory_gib]
        args.extend(["--management-config", self.management_path])
        if self.remote:
            if (not re.fullmatch(r"(?:[\w.-]+@)?[\w.:-]+", self.remote)
                    or self.remote.rsplit("@", 1)[-1].startswith("-") or not self.helper
                    or not self.port.isdigit() or not 1 <= int(self.port) <= 65535):
                raise OperationsError("Operations SSH host, port or helper configuration is invalid.", 503)
            command = shlex.join([self.python, self.helper, *args])
            from core.platform_compat import _ssh_exec_argv
            argv = _ssh_exec_argv(self.remote, self.port, remote_cmd=command,
                                  connect_timeout=10, strict_host_key_checking=True)
            argv[1:1] = ["-o", "BatchMode=yes"]
        else:
            helper = self.helper or Path(__file__).resolve().parents[2] / "scripts" / "odysseus-litellm.py"
            argv = [sys.executable, str(helper), *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(data), timeout=360)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                proc.kill()
                await proc.communicate()
                raise
            output_limit = (8 if action == "audit_detail" else 2) * MAX_CONFIG_BYTES
            if proc.returncode or len(stdout) > output_limit:
                raise OperationsError("Operations host helper could not be reached. Check its installation and SSH access.", 502)
            result = json.loads(stdout)
            if not isinstance(result, dict):
                raise ValueError()
        except (OSError, ValueError):
            raise OperationsError("Operations host helper returned no valid response.", 502) from None
        except asyncio.TimeoutError:
            raise OperationsError("Host operation timed out. Check LiteLLM status before retrying.", 504) from None
        if "error" in result:
            raise OperationsError(result["error"], result.get("status", 502))
        return result
