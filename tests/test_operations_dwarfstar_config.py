"""Context jobs preserve unit settings and recover failed DwarfStar starts."""

import json
from pathlib import Path

import pytest

from services.operations import dwarfstar_config as config
from services.operations.litellm_config import OperationsError
from services.operations.management import Management


UNIT = b"""[Unit]
Description=DwarfStar
[Service]
WorkingDirectory=%h/dwarfstar
ExecStart=%h/dwarfstar/ds4-server --rocm -m /mnt/models/flash.gguf --ctx 4096 --host 127.0.0.1 --port 8000
Restart=no
UMask=0077
[Install]
WantedBy=default.target
"""


@pytest.fixture
def host(tmp_path, monkeypatch):
    unit = tmp_path / "dwarfstar.service"
    unit.write_bytes(UNIT)
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"services": {"dwarfstar": {"unit": unit.name, "scope": "user"}},
                                    "dwarfstar_setup": {"unit_file": str(unit)}}))
    management = Management(str(settings), "http://strata", "http://ollama", "http://dwarfstar")
    state = {"running": True, "commands": [], "fail_context": None, "dropins": "", "reload": "no"}

    def systemctl(*arguments):
        state["commands"].append(arguments)
        if arguments[0] == "show":
            return (f"FragmentPath={unit}\nDropInPaths={state['dropins']}\n"
                    f"ActiveState={'active' if state['running'] else 'inactive'}\nNeedDaemonReload={state['reload']}\n")
        if arguments[0] == "stop":
            state["running"] = False
        if arguments[0] == "start":
            state["running"] = True
        return ""

    def inventory(url):
        context = config.context_argument(unit.read_bytes())[1]
        if not state["running"] or context == state["fail_context"]:
            return {"status": "unavailable"}
        return {"status": "ok", "model": "DeepSeek V4 Flash", "max_context": context}

    def await_context(management, context, model, **kwargs):
        live = inventory(management.dwarfstar_url)
        if live.get("status") != "ok" or live.get("max_context") != context or live.get("model") != model:
            raise OperationsError("Startup failed", 502)

    monkeypatch.setattr(config, "systemctl", systemctl)
    monkeypatch.setattr(config, "inventory", inventory)
    monkeypatch.setattr(config, "await_context", await_context)
    folder = tmp_path / "job"
    folder.mkdir()
    return management, unit, state, folder


def reviewed(management, context=65536):
    current = config.snapshot(management)
    return config.review(management, {"revision": current["revision"], "context": context})


def test_running_service_changes_only_context_and_verifies_restart(host):
    management, unit, state, folder = host
    specification = reviewed(management)
    assert state["running"] is True
    assert all(command[0] == "show" for command in state["commands"])
    result = config.run(management, specification, folder)
    assert unit.read_bytes() == UNIT.replace(b"--ctx 4096", b"--ctx 65536")
    assert result["active_context"] == 65536
    assert state["running"] is True
    assert (folder / "dwarfstar-backup/dwarfstar.service").read_bytes() == UNIT
    assert ("stop", "dwarfstar.service") in state["commands"]
    assert ("daemon-reload",) in state["commands"]
    assert ("start", "dwarfstar.service") in state["commands"]


def test_stopped_service_saves_context_without_loading_model(host):
    management, unit, state, folder = host
    state["running"] = False
    result = config.run(management, reviewed(management), folder)
    assert config.context_argument(unit.read_bytes())[1] == 65536
    assert result["active_context"] is None
    assert state["running"] is False
    assert not any(command[0] in ("start", "stop") for command in state["commands"])


def test_startup_failure_restores_exact_unit_and_previous_running_context(host):
    management, unit, state, folder = host
    state["fail_context"] = 65536
    with pytest.raises(OperationsError, match="Startup failed"):
        config.run(management, reviewed(management), folder)
    assert unit.read_bytes() == UNIT
    assert state["running"] is True
    assert json.loads((folder / "result.json").read_text())["restored"] is True


def test_stale_review_cannot_stop_or_edit_service(host):
    management, unit, state, folder = host
    specification = reviewed(management)
    unit.write_bytes(UNIT + b"# external edit\n")
    with pytest.raises(OperationsError, match="changed"):
        config.run(management, specification, folder)
    assert not any(command[0] == "stop" for command in state["commands"])
    assert unit.read_bytes().endswith(b"# external edit\n")


def test_modified_context_requires_new_review_digest(host):
    management, unit, state, folder = host
    specification = reviewed(management)
    specification["context"] = 131072
    with pytest.raises(OperationsError, match="no longer matches"):
        config.run(management, specification, folder)
    assert unit.read_bytes() == UNIT
    assert not any(command[0] == "stop" for command in state["commands"])


@pytest.mark.parametrize("value", [True, 4096.5, "65536", 0, 262145])
def test_invalid_context_is_rejected_before_service_changes(host, value):
    management, unit, state, folder = host
    with pytest.raises(OperationsError, match="whole number"):
        reviewed(management, value)
    assert unit.read_bytes() == UNIT
    assert not any(command[0] == "stop" for command in state["commands"])


@pytest.mark.parametrize("raw", [UNIT.replace(b"--ctx 4096", b"--ctx 4096 -c 8192"),
                                UNIT.replace(b"--ctx 4096", b"--ctx '$CONTEXT'"),
                                UNIT.replace(b"ds4-server", b"launcher.sh"),
                                UNIT.replace(b"--ctx 4096", b"--ctx \\\n4096")])
def test_ambiguous_or_indirect_commands_are_not_rewritten(raw):
    with pytest.raises(OperationsError, match="direct ds4-server"):
        config.context_argument(raw)


@pytest.mark.parametrize("field,value", [("dropins", "/some/override.conf"), ("reload", "yes")])
def test_service_overrides_and_pending_reload_block_apply(host, field, value):
    management, unit, state, folder = host
    state[field] = value
    assert config.snapshot(management)["configured"] is False
    with pytest.raises(OperationsError, match="drop-ins"):
        config.run(management, config.review(management, {
            "revision": config.fingerprint(management, config.profile(management)), "context": 65536}), folder)
    assert unit.read_bytes() == UNIT


def test_recovery_preserves_external_edits_and_reports_unverified_state(host, monkeypatch):
    management, unit, state, folder = host
    def externally_changed(*args, **kwargs):
        unit.write_bytes(unit.read_bytes() + b"# later operator change\n")
        raise OperationsError("Startup failed", 502)
    monkeypatch.setattr(config, "await_context", externally_changed)
    with pytest.raises(OperationsError, match="edited externally"):
        config.run(management, reviewed(management), folder)
    assert unit.read_bytes().endswith(b"# later operator change\n")
    assert json.loads((folder / "result.json").read_text())["restored"] is False


def test_queued_job_carries_exact_review_and_uses_shared_job_queue(host, monkeypatch):
    management, unit, state, folder = host
    from services.operations import jobs
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda owner, spec: queued.append(spec) or {"status": "queued"})
    specification = reviewed(management)
    assert config.enqueue_apply(management, specification)["status"] == "queued"
    assert queued[0]["kind"] == "dwarfstar_context"
    assert queued[0]["review_digest"] == specification["review_digest"]
    assert unit.read_bytes() == UNIT


def test_system_service_cannot_be_edited(host):
    management, unit, state, folder = host
    management.settings["services"]["dwarfstar"]["scope"] = "system"
    assert config.snapshot(management)["configured"] is False
    assert unit.read_bytes() == UNIT


def test_cancellation_during_restart_restores_previous_context(host, monkeypatch):
    management, unit, state, folder = host
    previous = config.systemctl
    def cancel_start(*arguments):
        result = previous(*arguments)
        if arguments[0] == "start" and config.context_argument(unit.read_bytes())[1] == 65536:
            config.signal.getsignal(config.signal.SIGTERM)(config.signal.SIGTERM, None)
        return result
    monkeypatch.setattr(config, "systemctl", cancel_start)
    with pytest.raises(InterruptedError, match="cancelled"):
        config.run(management, reviewed(management), folder)
    assert unit.read_bytes() == UNIT
    assert state["running"] is True
    assert json.loads((folder / "result.json").read_text())["restored"] is True


def test_startup_verification_checks_model_as_well_as_context(monkeypatch):
    management = Management(None, "http://strata", "http://ollama", "http://dwarfstar")
    monkeypatch.setattr(config, "inventory", lambda url: {"status": "ok", "model": "Wrong model", "max_context": 65536})
    ticks = iter([0, 0, 10])
    monkeypatch.setattr(config.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(config.time, "sleep", lambda seconds: None)
    with pytest.raises(OperationsError, match="requested model and context"):
        config.await_context(management, 65536, "DeepSeek V4 Flash", timeout=5)


def test_context_routes_forward_only_after_admin_and_origin_checks(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes import operations_routes

    calls = []
    class Host:
        async def run(self, action, values):
            calls.append((action, values))
            return {"action": action}

    monkeypatch.setattr(operations_routes, "require_admin", lambda request: None)
    app = FastAPI()
    app.include_router(operations_routes.setup_operations_routes(Host()))
    with TestClient(app) as client:
        assert client.get('/api/operations/dwarfstar/config').json()["action"] == "dwarfstar_config"
        assert client.post('/api/operations/dwarfstar/preview', json={"context": 65536}).json()["action"] == "dwarfstar_preview"
        assert client.post('/api/operations/dwarfstar/apply', json={"context": 65536}).json()["action"] == "dwarfstar_apply"
        count = len(calls)
        assert client.post('/api/operations/dwarfstar/apply', json={}, headers={"Origin": "https://other.example"}).status_code == 403
        assert len(calls) == count

        def forbidden(request):
            raise HTTPException(403, "Admin required")
        monkeypatch.setattr(operations_routes, "require_admin", forbidden)
        assert client.get('/api/operations/dwarfstar/config').status_code == 403
        assert client.post('/api/operations/dwarfstar/apply', json={}).status_code == 403
        assert len(calls) == count
