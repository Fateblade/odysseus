"""Native lifecycle adapters never start a competing engine before verified release."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.operations.inference_backends import NativeEngines
from services.operations.inference_lifecycle import runtime_lease, runtime_busy
from services.operations.management import Management
from services.operations.litellm_config import OperationsError


class Host:
    def __init__(self):
        for backend in ("maya", "maya_glm", "dwarfstar", "strata", "ollama"):
            setattr(self, backend + "_url", "http://" + backend)
        self.states = {"maya": "stopped", "maya_glm": "running", "dwarfstar": "stopped", "strata": "running", "ollama": "running"}
        self.values = {"maya": {"status": "unavailable", "loaded": False, "model": "ds"},
                       "maya_glm": {"status": "ok", "loaded": True, "model": "glm"},
                       "dwarfstar": {"status": "stopped", "loaded": False},
                       "strata": {"status": "ok", "loaded": True, "model": "qwen"},
                       "ollama": {"loaded_available": True, "loaded": [{"name": "qwen:8b"}]}}
        self.events = []

    def residency(self):
        return deepcopy(self.values)

    def service_state(self, name):
        return {"state": self.states[name]}

    def service(self, name, action):
        self.events.append((name, action))
        self.states[name] = "running" if action in ("start", "restart") else "stopped"
        self.values[name].update(status="ok" if self.states[name] == "running" else "stopped", loaded=self.states[name] == "running")

    def api(self, base, path, body, **kwargs):
        name = base.split("//")[1]
        self.events.append((name, path))
        if path == "/unload":
            self.values[name]["loaded"] = False
        elif path == "/load":
            self.values[name]["loaded"] = True
        elif path == "/api/generate":
            if body["keep_alive"] == 0:
                self.values[name]["loaded"] = [row for row in self.values[name]["loaded"] if row["name"] != body["model"]]
            else:
                self.values[name]["loaded"] = [{"name": body["model"]}]
        return {"status": "ok"}


def test_switch_releases_every_competitor_before_start(monkeypatch):
    host = Host()
    monkeypatch.setattr("services.operations.inference_backends.api", host.api)
    NativeEngines(host, probe=lambda backend: host.residency()[backend]).prepare("maya", "ds")
    assert host.events[-1] == ("maya", "start")
    assert ("maya_glm", "stop") in host.events
    assert ("strata", "/unload") in host.events
    assert ("ollama", "/api/generate") in host.events
    assert host.values["maya"]["loaded"] is True


def test_unknown_competitor_never_permits_start(monkeypatch):
    host = Host()
    host.states["maya_glm"] = "unknown"
    monkeypatch.setattr("services.operations.inference_backends.api", host.api)
    with pytest.raises(OperationsError, match="not confirmed stopped"):
        NativeEngines(host, probe=lambda backend: host.residency()[backend]).prepare("maya", "ds")
    assert ("maya", "start") not in host.events


def test_busy_strata_does_not_permit_competing_start(monkeypatch):
    host = Host()
    monkeypatch.setattr("services.operations.inference_backends.api", lambda *args, **kw: {"status": "busy"})
    with pytest.raises(OperationsError, match="refused unloading"):
        NativeEngines(host, probe=lambda backend: host.residency()[backend]).prepare("maya", "ds")
    assert ("maya", "start") not in host.events


def test_ollama_route_replaces_other_ollama_models(monkeypatch):
    host = Host()
    monkeypatch.setattr("services.operations.inference_backends.api", host.api)
    NativeEngines(host, probe=lambda backend: host.residency()[backend]).prepare("ollama", "other:8b")
    assert host.values["ollama"]["loaded"] == [{"name": "other:8b"}]
    assert not any(host.values[backend]["loaded"] for backend in ("maya", "maya_glm", "dwarfstar", "strata"))


def test_runtime_lease_blocks_manual_mutations(tmp_path):
    path = tmp_path / "management.json"
    path.write_text('{}')
    host = Management(str(path), "", "")
    with runtime_lease(host):
        assert runtime_busy(host)
        with pytest.raises(OperationsError, match="Inference or host maintenance"):
            host.execute({"target": "strata", "operation": "load"})
    assert not runtime_busy(host)
