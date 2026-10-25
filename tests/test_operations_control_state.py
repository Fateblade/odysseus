"""Buttons and mutations use confirmed process state, not model readiness."""

import json
from types import SimpleNamespace

import pytest

from services.operations.control_state import require_transition
from services.operations.litellm_config import OperationsError
from services.operations.management import Management


@pytest.mark.parametrize("active, load, result", [
    ("active", "loaded", "running"), ("inactive", "loaded", "stopped"), ("failed", "loaded", "stopped"),
    ("activating", "loaded", "transitioning"), ("deactivating", "loaded", "transitioning"),
    ("reloading", "loaded", "transitioning"), ("inactive", "not-found", "unknown"),
    ("inactive", "masked", "unknown"), ("unexpected", "loaded", "unknown"),
])
def test_service_state_requires_loaded_unit(active, load, result):
    host = Management(None, "", "")
    host.service = lambda *args, **kw: SimpleNamespace(returncode=0, stdout=f"LoadState={load}\nActiveState={active}\nSubState=running\n".encode())
    assert host.service_state("maya")["state"] == result


@pytest.mark.parametrize("state", ["running", "stopped", "transitioning", "unknown"])
@pytest.mark.parametrize("operation", ["start", "stop", "restart"])
def test_only_valid_transitions_are_accepted(state, operation):
    allowed = state == ("stopped" if operation == "start" else "running")
    if allowed:
        require_transition({"state": state}, operation)
    else:
        with pytest.raises(OperationsError) as error:
            require_transition({"state": state}, operation)
        assert error.value.status == 409


def test_state_read_failure_never_assumes_stopped():
    host = Management(None, "", "")
    def inaccessible(*args, **kwargs):
        raise OSError("Unavailable")
    host.service = inaccessible
    assert host.service_state("maya") == {"state": "unknown"}


def test_state_is_rechecked_before_mutation_and_queue(tmp_path, monkeypatch):
    from services.operations import jobs
    path = tmp_path / "management.json"
    path.write_text(json.dumps({"services": {"maya": "maya.service"}}))
    host = Management(str(path), "", "", maya_url="http://maya")
    host.service_state = lambda _: {"state": "running"}
    monkeypatch.setattr(jobs, "enqueue", lambda *_: pytest.fail("Redundant operation must not be queued"))
    with pytest.raises(OperationsError, match="Cannot start"):
        host.execute({"target": "service", "name": "maya", "operation": "start"})


def test_container_states_are_filtered_and_conservative(tmp_path, monkeypatch):
    from services.operations import overview
    path = tmp_path / "management.json"
    path.write_text(json.dumps({"containers": ["running", "stopped", "starting", "missing"]}))
    host = Management(str(path), "", "")
    monkeypatch.setattr(overview, "containers", lambda: {"status": "ok", "items": [
        {"name": "running", "state": "running"}, {"name": "stopped", "state": "exited"},
        {"name": "starting", "state": "restarting"}, {"name": "unlisted", "state": "running"}]})
    result = host.container_states()
    assert {key: value["state"] for key, value in result.items()} == {
        "running": "running", "stopped": "stopped", "starting": "transitioning", "missing": "unknown"}


def test_system_service_state_reads_do_not_require_mutation_sudo(tmp_path, monkeypatch):
    import services.operations.management as module
    path = tmp_path / "management.json"
    path.write_text(json.dumps({"services": {"ollama": {"unit": "ollama.service", "scope": "system", "sudo": True}}}))
    host = Management(str(path), "", "")
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kw: calls.append((command, kw)) or SimpleNamespace(returncode=0, stdout=b"LoadState=loaded\nActiveState=active\n"))
    assert host.service_state("ollama")["state"] == "running"
    assert calls[0][0][0] == "systemctl" and calls[0][1]["timeout"] == 5


@pytest.mark.parametrize("loaded", [True, False, None])
@pytest.mark.parametrize("operation", ["load", "unload"])
def test_strata_mutations_require_confirmed_opposite_residency(monkeypatch, loaded, operation):
    from services.operations import management
    from services.operations.control_state import require_strata_transition
    monkeypatch.setattr(management, "api", lambda *args, **kwargs: {"status": "ok", "loaded": loaded})
    if isinstance(loaded, bool) and loaded == (operation == "unload"):
        require_strata_transition("http://strata", operation)
    else:
        with pytest.raises(OperationsError, match="Cannot " + operation):
            require_strata_transition("http://strata", operation)


def test_strata_state_is_rechecked_before_queueing(monkeypatch):
    from services.operations import jobs, management
    host = Management(None, "http://strata", "")
    def health(base, path, body, **kwargs):
        assert path == "/health" and kwargs["method"] == "GET"
        return {"status": "ok", "loaded": True}
    monkeypatch.setattr(management, "api", health)
    monkeypatch.setattr(jobs, "enqueue", lambda *_: pytest.fail("Redundant load must not be queued"))
    with pytest.raises(OperationsError, match="Cannot load"):
        host.execute({"target": "strata", "operation": "load"})
