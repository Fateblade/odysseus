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
    def __init__(self, settings_path, strata_url, ollama_url, dwarfstar_url="", maya_url="", maya_glm_url=""):
        self.path = Path(settings_path).expanduser() if settings_path else None
        try:
            self.settings = json.loads(self.path.read_text(encoding="utf-8")) if self.path and self.path.exists() else {}
        except json.JSONDecodeError as error:
            raise OperationsError(f"Host management configuration is invalid JSON at line {error.lineno}, column {error.colno}. Check management.json.", 503) from None
        except (OSError, UnicodeError):
            raise OperationsError("Host management configuration cannot be read as UTF-8. Check management.json permissions and encoding for the helper user.", 503) from None
        if not isinstance(self.settings, dict) or not isinstance(self.settings.get("services", {}), dict):
            raise OperationsError("Host management configuration and its services entry must be JSON objects. Merge the GLM entries into the existing management.json object.", 503)
        for setting in self.settings.get("services", {}).values():
            if not isinstance(setting, (str, dict)):
                raise OperationsError("Each host management service must be a unit-name string or an object containing unit and scope.", 503)
        self.strata_url = strata_url
        self.ollama_url = ollama_url
        self.dwarfstar_url = dwarfstar_url
        self.maya_url = maya_url
        self.maya_glm_url = maya_glm_url
        self.audit_actor = {}
        if self.maya_glm_url:
            if self.maya_url.rstrip("/") == self.maya_glm_url.rstrip("/"):
                raise OperationsError("Maya GLM needs its own endpoint, separate from Maya DS41F.", 503)
            services = self.settings.get("services", {})
            def unit(name):
                setting = services.get(name)
                return setting if isinstance(setting, str) else (setting or {}).get("unit")
            if unit("maya") and unit("maya") == unit("maya_glm"):
                raise OperationsError("Maya GLM and DS41F must use different systemd units.", 503)

    def catalog(self):
        names = ["strata", "ollama"] + (["dwarfstar"] if self.dwarfstar_url else []) + (["maya"] if self.maya_url else []) + (["maya_glm"] if self.maya_glm_url else [])
        return {"containers": self.settings.get("containers", []),
                "protected_containers": self.settings.get("protected_containers", ["odysseus-odysseus-1"]),
                "services": self.settings.get("services", {}),
                "unconfigured_services": [name for name in names if name not in self.settings.get("services", {})],
                "scripts_enabled": bool(self.settings.get("scripts_root")),
                "configured": True, **self.control_states()}

    def service_state(self, name):
        try:
            result = self.service(name, "state", check=False)
            fields = dict(line.split("=", 1) for line in result.stdout.decode().splitlines() if "=" in line)
            active = fields.get("ActiveState")
            state = "unknown"
            if result.returncode == 0 and fields.get("LoadState") == "loaded":
                state = "running" if active == "active" else "stopped" if active in ("inactive", "failed") else "transitioning" if active in ("activating", "deactivating", "reloading", "refreshing") else "unknown"
            return {"state": state, "active_state": active, "sub_state": fields.get("SubState")}
        except (OperationsError, OSError, ValueError, subprocess.TimeoutExpired):
            return {"state": "unknown"}

    def container_states(self):
        names = self.settings.get("containers", [])
        result = {name: {"state": "unknown"} for name in names}
        if not names:
            return result
        from services.operations.overview import containers
        inventory = containers()
        if inventory.get("status") == "ok":
            for row in inventory["items"]:
                if row["name"] in result:
                    raw = row.get("state")
                    state = "running" if raw in ("running", "paused") else "stopped" if raw in ("created", "exited") else "transitioning" if raw in ("restarting", "removing") else "unknown"
                    result[row["name"]] = {"state": state, "docker_state": raw}
        return result

    def control_states(self):
        names = self.settings.get("services", {})
        with ThreadPoolExecutor(max_workers=4) as executor:
            states = {name: executor.submit(self.service_state, name) for name in names}
            containers = executor.submit(self.container_states)
            return {"service_states": {name: future.result() for name, future in states.items()},
                    "container_states": containers.result()}

    def residency(self):
        from services.operations.dwarfstar import inventory
        from services.operations.overview import ollama, strata
        from services.operations.maya import inventory as maya_inventory, glm_inventory

        with ThreadPoolExecutor(max_workers=6) as executor:
            controls_result = executor.submit(self.control_states)
            strata_result = executor.submit(strata, self.strata_url)
            ollama_result = executor.submit(ollama, self.ollama_url)
            dwarfstar_result = executor.submit(inventory, self.dwarfstar_url, self)
            maya_result = executor.submit(maya_inventory, self.maya_url)
            glm_result = executor.submit(glm_inventory, self.maya_glm_url)
            return {"configured": True, "strata": strata_result.result(),
                    "ollama": ollama_result.result(), "dwarfstar": dwarfstar_result.result(), "maya": maya_result.result(), "maya_glm": glm_result.result(), "sampled_at": time.time(), **controls_result.result()}

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
        elif operation == "state":
            command = ["systemctl", *flags, "show", unit, "--property=LoadState,ActiveState,SubState"]
        else:
            command = ["systemctl", *flags, operation, unit, "--no-pager"]
        if setting.get("sudo") and scope == "system" and operation != "state":
            command = ["sudo", "-n", *command]
        result = subprocess.run(command, capture_output=True, timeout=5 if operation == "state" else 90)
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
            if operation in ("start", "stop", "restart"):
                from services.operations.control_state import require_transition
                require_transition(self.container_states().get(name, {}), operation)
            args = ("logs", "--tail", "200", "--timestamps") if operation == "logs" else (operation,)
            output = docker_command(name, *args)
            return {"status": "ok", "log": output[-65536:].decode(errors="replace") if operation == "logs" else ""}
        if target == "service":
            if operation not in ("start", "stop", "restart", "status", "logs"):
                raise OperationsError("Unknown service operation.")
            if payload.get("name") in ("maya", "maya_glm", "dwarfstar") and operation in ("start", "stop", "restart"):
                backend = payload["name"]
                if not getattr(self, backend + "_url") or backend not in self.settings.get("services", {}):
                    raise OperationsError("Configure an explicit systemd unit and endpoint for this allowlisted inference service first.", 503)
                from services.operations.control_state import require_transition
                require_transition(self.service_state(backend), operation)
                from services.operations.jobs import enqueue
                return enqueue(self, {"kind": "inference_service" if backend == "dwarfstar" else "maya_service", "backend": backend, "operation": operation, "timeout": 900})
            if operation in ("start", "stop", "restart") and payload.get("name") in self.settings.get("services", {}):
                from services.operations.control_state import require_transition
                require_transition(self.service_state(payload["name"]), operation)
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
