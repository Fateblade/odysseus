"""Load and release configured native engines under an inference lease."""

import time

from services.operations.exclusivity import EXCLUSIVE
from services.operations.litellm_config import OperationsError
from services.operations.management import api
from services.operations.audit import directory, emit


class NativeEngines:
    def __init__(self, management, startup_timeout=600, probe=None):
        self.management = management
        self.startup_timeout = startup_timeout
        self.probe = probe

    def live(self, backend):
        if self.probe:
            return self.probe(backend)
        from services.operations import overview, maya, dwarfstar
        url = getattr(self.management, backend + "_url", "")
        if backend == "dwarfstar":
            return dwarfstar.inventory(url, self.management)
        probe = {"strata": overview.strata, "ollama": overview.ollama, "maya": maya.inventory, "maya_glm": maya.glm_inventory}[backend]
        return probe(url)

    def stopped(self, backend):
        state = self.management.service_state(backend)["state"]
        if state != "stopped":
            raise OperationsError(f"{backend} is not confirmed stopped; cannot switch engines.", 409)

    def release(self, backend, model=None):
        self.record("routing.release_requested", backend, model)
        self._release(backend, model)
        self.record("routing.release_confirmed", backend, model)

    def record(self, event, backend, model):
        if hasattr(self.management, "settings"):
            emit(directory(self.management), event, source="inference-router", backend=backend, model=model,
                 **getattr(self.management, "routing_actor", {}))

    def _release(self, backend, model=None):
        host = self.management
        value = self.live(backend)
        if backend in EXCLUSIVE:
            state = host.service_state(backend)["state"]
            if state == "running":
                host.service(backend, "stop")
            self.stopped(backend)
            if self.live(backend).get("loaded"):
                raise OperationsError(f"{backend} still answers as loaded after stop.", 409)
        elif backend == "strata":
            if value.get("loaded") is True:
                result = api(host.strata_url, "/unload", {}, timeout=300)
                if result.get("status") in ("busy", "unsupported"):
                    raise OperationsError("Strata refused unloading; no other engine was started.", 409)
                if self.live("strata").get("loaded") is not False:
                    raise OperationsError("Strata unload was not confirmed.", 409)
            elif value.get("loaded") is not False:
                self.stopped(backend)
        elif backend == "ollama":
            if not value.get("loaded_available"):
                self.stopped(backend)
                return
            for row in value.get("loaded", []):
                if model is None or row["name"] == model:
                    api(host.ollama_url, "/api/generate", {"model": row["name"], "keep_alive": 0, "stream": False}, timeout=300)
            remaining = self.live("ollama")
            if not remaining.get("loaded_available") or any(model is None or row["name"] == model for row in remaining.get("loaded", [])):
                raise OperationsError("Ollama unload was not confirmed.", 409)

    def prepare(self, backend, model):
        host = self.management
        for other in (*EXCLUSIVE, "strata", "ollama"):
            if other == backend or not getattr(host, other + "_url", ""):
                continue
            self.release(other)
        if backend == "ollama":
            self.ensure_server(backend)
            for row in self.live("ollama").get("loaded", []):
                if row["name"] != model:
                    self.release("ollama", row["name"])
            api(host.ollama_url, "/api/generate", {"model": model, "keep_alive": -1, "stream": False}, timeout=self.startup_timeout)
            if not any(row["name"] == model for row in self.live("ollama").get("loaded", [])):
                raise OperationsError("Ollama model readiness was not confirmed.", 502)
            return
        self.ensure_server(backend)
        value = self.live(backend)
        if backend == "strata" and value.get("loaded") is False:
            result = api(host.strata_url, "/load", {}, timeout=self.startup_timeout)
            if result.get("status") in ("busy", "unsupported"):
                raise OperationsError("Strata refused loading.", 409)
        if backend in EXCLUSIVE and value.get("loaded") is False:
            host.service(backend, "restart")
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            value = self.live(backend)
            if value.get("loaded") is True and value.get("status") == "ok":
                if model not in [value.get("model"), *(value.get("aliases") or [])]:
                    raise OperationsError("Loaded model does not match the configured inference route.", 409)
                return
            time.sleep(1)
        raise OperationsError("Engine startup timed out; inspect its service logs.", 504)

    def pin(self, backend, model):
        if backend == "ollama":
            api(self.management.ollama_url, "/api/generate", {"model": model, "keep_alive": -1, "stream": False}, timeout=30)

    def ensure_server(self, backend):
        host = self.management
        value = self.live(backend)
        ready = value.get("loaded_available") if backend == "ollama" else value.get("status") in ("ok", "degraded", "unloaded")
        if ready:
            return
        state = host.service_state(backend)["state"]
        if state == "stopped":
            host.service(backend, "start")
        elif state != "running":
            raise OperationsError(f"{backend} has an unknown or changing service state.", 409)
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            value = self.live(backend)
            ready = value.get("loaded_available") if backend == "ollama" else value.get("status") in ("ok", "degraded", "unloaded")
            if ready:
                return
            time.sleep(1)
        raise OperationsError("Engine server readiness timed out.", 504)
