"""Allowlisted host controls; long operations run as durable jobs."""

import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import NoRedirect, docker_command


def api(base, path, body, method="POST", timeout=30):
    request = Request(base.rstrip("/") + path, data=json.dumps(body).encode(), method=method,
                      headers={"Content-Type": "application/json", "Origin": base.rstrip("/")})
    try:
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise OperationsError("Backend response exceeds the size limit.", 502)
            return json.loads(raw) if raw else {"status": "ok"}
    except HTTPError as error:
        raise OperationsError(f"Backend rejected the operation (HTTP {error.code}).", 409 if error.code == 409 else 502) from None
    except (URLError, TimeoutError, ValueError):
        raise OperationsError("Backend is unavailable or returned an invalid response.", 502) from None


def model_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(c.isspace() or ord(c) < 32 for c in value):
        raise OperationsError("Supply a model name without whitespace (at most 512 characters).")
    return value


class Management:
    def __init__(self, settings_path, strata_url, ollama_url, dwarfstar_url="", maya_url=""):
        self.path = Path(settings_path).expanduser() if settings_path else None
        self.settings = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}
        self.strata_url = strata_url
        self.ollama_url = ollama_url
        self.dwarfstar_url = dwarfstar_url
        self.maya_url = maya_url
        self.audit_actor = {}

    def catalog(self):
        names = ["strata", "ollama"] + (["dwarfstar"] if self.dwarfstar_url else []) + (["maya"] if self.maya_url else [])
        return {"containers": self.settings.get("containers", []),
                "protected_containers": self.settings.get("protected_containers", ["odysseus-odysseus-1"]),
                "services": self.settings.get("services", {}),
                "unconfigured_services": [name for name in names if name not in self.settings.get("services", {})],
                "scripts_enabled": bool(self.settings.get("scripts_root")),
                "configured": True}

    def residency(self):
        from services.operations.dwarfstar import inventory
        from services.operations.overview import ollama, strata
        from services.operations.maya import inventory as maya_inventory

        with ThreadPoolExecutor(max_workers=4) as executor:
            strata_result = executor.submit(strata, self.strata_url)
            ollama_result = executor.submit(ollama, self.ollama_url)
            dwarfstar_result = executor.submit(inventory, self.dwarfstar_url)
            maya_result = executor.submit(maya_inventory, self.maya_url)
            return {"configured": True, "strata": strata_result.result(),
                    "ollama": ollama_result.result(), "dwarfstar": dwarfstar_result.result(), "maya": maya_result.result(), "sampled_at": time.time()}

    def service(self, name, operation, check=True):
        setting = self.settings.get("services", {}).get(name)
        setting = {"unit": setting, "scope": "user"} if isinstance(setting, str) else setting or {}
        unit = setting.get("unit")
        if not unit or not re.fullmatch(r"[\w@.-]+\.service", unit):
            raise OperationsError("Configure an explicit systemd unit for this service first.", 503)
        scope = setting.get("scope", "user")
        if scope not in ("user", "system"):
            raise OperationsError("Configured service scope must be user or system.", 503)
        flags = ["--user"] if scope == "user" else []
        if operation == "logs":
            command = ["journalctl", *flags, "-u", unit, "-n", "200", "--no-pager"]
        elif operation == "show":
            command = ["systemctl", *flags, "show", unit, "--property=ExecStart", "--value"]
        else:
            command = ["systemctl", *flags, operation, unit, "--no-pager"]
        if setting.get("sudo") and scope == "system":
            command = ["sudo", "-n", *command]
        result = subprocess.run(command, capture_output=True, timeout=90)
        if check and result.returncode:
            raise OperationsError("The service operation failed. Check the unit and helper user's systemd permissions on the host.", 502)
        return result

    def execute(self, payload):
        target, operation = payload.get("target"), payload.get("operation")
        if target == "docker":
            name = payload.get("name")
            if name not in self.settings.get("containers", []):
                raise OperationsError("This container is not in the host management allowlist.", 403)
            if operation not in ("start", "stop", "restart", "logs"):
                raise OperationsError("Unknown container operation.")
            if name in self.settings.get("protected_containers", ["odysseus-odysseus-1"]) and operation in ("stop", "restart"):
                raise OperationsError("This container is protected from dashboard stop/restart.", 403)
            args = ("logs", "--tail", "200", "--timestamps") if operation == "logs" else (operation,)
            output = docker_command(name, *args)
            return {"status": "ok", "log": output[-65536:].decode(errors="replace") if operation == "logs" else ""}
        if target == "service":
            if operation not in ("start", "stop", "restart", "status", "logs"):
                raise OperationsError("Unknown service operation.")
            if payload.get("name") == "maya" and operation in ("start", "stop", "restart"):
                if not self.maya_url or "maya" not in self.settings.get("services", {}):
                    raise OperationsError("Configure the Maya endpoint and allowlisted service first.", 503)
                from services.operations.jobs import enqueue
                return enqueue(self, {"kind": "maya_service", "operation": operation, "timeout": 900})
            result = self.service(payload.get("name"), operation, check=operation != "status")
            return {"status": "ok", "log": (result.stdout + result.stderr)[-65536:].decode(errors="replace")}
        if target == "strata":
            if operation not in ("load", "unload"):
                raise OperationsError("Unknown Strata model operation.")
            from services.operations.jobs import enqueue
            return enqueue(self, {"kind": "strata", "operation": operation, "timeout": 300})
        if target == "ollama":
            name = model_name(payload.get("name"))
            if operation == "pull":
                from services.operations.jobs import enqueue
                return enqueue(self, {"kind": "pull", "model": name, "timeout": 7200})
            if operation in ("show", "delete", "copy"):
                body = {"model": name} if operation != "copy" else {"source": name, "destination": model_name(payload.get("destination"))}
                result = api(self.ollama_url, "/api/" + operation, body, "DELETE" if operation == "delete" else "POST")
                return {"status": "ok", "details": result}
            if operation in ("load", "unload"):
                from services.operations.jobs import enqueue
                return enqueue(self, {"kind": "residency", "model": name, "operation": operation, "timeout": 300})
        raise OperationsError("Unknown management operation.")
