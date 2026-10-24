"""Maya settings transactions preserve host config and service state."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.operations import maya_config, maya_settings
from services.operations.litellm_config import OperationsError
from services.operations.management import Management


@pytest.fixture
def host(tmp_path, monkeypatch):
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "tensors.txt").write_text("t mtp.weight 1 2\n")
    config = tmp_path / "maya.json"
    config.write_text(json.dumps({"args": ["--pack", pack.as_posix(), "--max-context", "4096", "--dspark", "none"],
                                  "private_key": "keep-private", "backend": "hip"}))
    unit = tmp_path / "maya-xs.service"
    unit.write_text(f"[Service]\nExecStart=/usr/bin/python3 -m serve.server --config {config.as_posix()}\n")
    path = tmp_path / "management.json"
    path.write_text(json.dumps({"services": {"maya": {"unit": unit.name, "scope": "user"}},
                               "maya_setup": {"config_file": str(config), "unit_file": str(unit), "maximum_context": 4096},
                               "unrelated": "preserve"}))
    management = Management(str(path), "", "", maya_url="http://maya")
    state = {"running": True, "calls": [], "fail_start": False}

    def service(name, operation):
        state["calls"].append(operation)
        if operation == "start" and state["fail_start"]:
            state["fail_start"] = False
            raise OperationsError("Startup failed", 502)
        state["running"] = operation == "start"
        return SimpleNamespace(stdout=b"", stderr=b"")

    def live(_):
        args = json.loads(config.read_text())["args"]
        return {"status": "ok", "max_context": int(args[args.index("--max-context") + 1]), "model": "deepseek"} if state["running"] else {"status": "unavailable"}

    def get_json(url):
        args = json.loads(config.read_text())["args"]
        return {"gpu_map": [{"drafter": 100 if args[args.index("--dspark") + 1] != "none" else 0}]} if url.endswith("/metrics") else {
            "min": 4096, "max": 262144, "context": live(None).get("max_context")}

    management.service = service
    monkeypatch.setattr(maya_settings, "systemctl", lambda *args: f"FragmentPath={unit}\nDropInPaths=\nActiveState={'active' if state['running'] else 'inactive'}\nNeedDaemonReload=no\n")
    monkeypatch.setattr(maya_settings, "inventory", live)
    monkeypatch.setattr(maya_settings, "get_json", get_json)
    monkeypatch.setattr(maya_settings, "run_service", lambda m, spec: m.service("maya", spec["operation"]))
    return management, config, path, state


def reviewed(host, context=8192, drafting=True):
    management = host[0]
    current = maya_config.snapshot(management)
    assert current["configured"], current
    return maya_config.review(management, {"revision": current["revision"], "context": context, "drafting": drafting})


def test_running_settings_restart_verify_and_preserve_private_fields(host, tmp_path):
    management, config, path, state = host
    change = reviewed(host)
    assert "private" not in json.dumps(change)
    result = maya_config.run(management, change, tmp_path)
    assert state["calls"] == ["stop", "start"]
    assert result["configured_context"] == 8192 and result["drafting"]
    saved = json.loads(config.read_text())
    assert saved["args"][-1] == "on" and saved["private_key"] == "keep-private"
    settings = json.loads(path.read_text())
    assert settings["maya_setup"]["maximum_context"] == 8192 and settings["unrelated"] == "preserve"
    assert (tmp_path / "maya-backup/manifest.json").is_file()


def test_stopped_settings_save_without_starting_and_off_unloads_drafter(host, tmp_path):
    management, config, _, state = host
    state["running"] = False
    value = json.loads(config.read_text())
    value["args"][-1] = "off"
    config.write_text(json.dumps(value))
    maya_config.run(management, reviewed(host, drafting=False), tmp_path)
    assert not state["calls"]
    assert json.loads(config.read_text())["args"][-1] == "none"


def test_start_failure_restores_both_files_and_running_state(host, tmp_path):
    management, config, path, state = host
    original = config.read_bytes(), path.read_bytes()
    change = reviewed(host)
    state["fail_start"] = True
    with pytest.raises(OperationsError, match="Startup failed"):
        maya_config.run(management, change, tmp_path)
    assert (config.read_bytes(), path.read_bytes()) == original
    assert state["running"] and state["calls"] == ["stop", "start", "stop", "start"]
    assert json.loads((tmp_path / "result.json").read_text())["restored"]


def test_stale_config_is_rejected_before_stopping_service(host, tmp_path):
    management, config, _, state = host
    change = reviewed(host)
    config.write_text(config.read_text() + "\n")
    with pytest.raises(OperationsError, match="state changed"):
        maya_config.run(management, change, tmp_path)
    assert not state["calls"]


@pytest.mark.parametrize("payload", [{"context": 262145}, {"context": True}, {"drafting": "on"}])
def test_limits_and_boolean_are_validated(host, payload):
    change = reviewed(host)
    with pytest.raises(OperationsError):
        maya_config.review(host[0], {**change, **payload})


def test_missing_mtp_rejects_enabling_but_allows_disabling(host):
    management, config, _, _ = host
    args = json.loads(config.read_text())["args"]
    (Path(args[1]) / "tensors.txt").write_text("t other.weight 1\n")
    with pytest.raises(OperationsError, match="no MTP drafter"):
        reviewed(host)
    assert not reviewed(host, drafting=False)["drafting"]


def test_queue_binds_drafting_and_exact_review(host, monkeypatch):
    change = reviewed(host)
    with pytest.raises(OperationsError, match="Review the exact"):
        maya_config.enqueue_apply(host[0], {**change, "drafting": False})
    from services.operations import jobs
    monkeypatch.setattr(jobs, "enqueue", lambda _, spec: spec)
    queued = maya_config.enqueue_apply(host[0], change)
    assert queued["drafting"] and queued["context"] == 8192 and queued["timeout"] == 1800


def test_drafter_residency_must_match_after_restart(host, monkeypatch):
    management = host[0]
    monkeypatch.setattr(maya_settings, "get_json", lambda _: {"gpu_map": [{"drafter": 0}]})
    times = iter([0, 0, 601])
    monkeypatch.setattr(maya_settings.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(maya_settings.time, "sleep", lambda _: None)
    with pytest.raises(OperationsError, match="drafter residency"):
        maya_settings.await_settings(management, 4096, "deepseek", True)


def test_cancellation_restores_files_and_previous_service(host, tmp_path, monkeypatch):
    management, config, path, state = host
    original = config.read_bytes(), path.read_bytes()
    change = reviewed(host)
    start = maya_settings.run_service
    first = True

    def cancelled(m, spec):
        nonlocal first
        if first:
            first = False
            raise InterruptedError("cancelled")
        return start(m, spec)

    monkeypatch.setattr(maya_settings, "run_service", cancelled)
    with pytest.raises(InterruptedError):
        maya_config.run(management, change, tmp_path)
    assert (config.read_bytes(), path.read_bytes()) == original
    assert state["running"]


def test_recovery_does_not_overwrite_external_edits(host, tmp_path, monkeypatch):
    management, config, _, _ = host
    change = reviewed(host)

    def external(*_):
        config.write_text('{"external":true}')
        raise OperationsError("Startup failed")

    monkeypatch.setattr(maya_settings, "run_service", external)
    with pytest.raises(OperationsError, match="edited externally"):
        maya_config.run(management, change, tmp_path)
    assert json.loads(config.read_text()) == {"external": True}
    assert not json.loads((tmp_path / "result.json").read_text())["restored"]


def test_service_state_change_invalidates_review(host, tmp_path):
    management, _, _, state = host
    change = reviewed(host)
    state["running"] = False
    with pytest.raises(OperationsError, match="state changed"):
        maya_config.run(management, change, tmp_path)
    assert not state["calls"]


def test_wrong_unit_config_refuses_management(host):
    management = host[0]
    unit = Path(management.settings["maya_setup"]["unit_file"])
    unit.write_text("[Service]\nExecStart=/usr/bin/python3 -m serve.server --config /some/other/config.json\n")
    assert not maya_config.snapshot(management)["configured"]
