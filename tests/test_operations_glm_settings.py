"""GLM native MTP edits share the reviewed Maya restart/recovery transaction."""

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
    config = tmp_path / "glm.json"
    config.write_text(json.dumps({"exe": str(tmp_path / "maya"), "backend": "hip", "gpu": [0],
        "model_name": "glm-5.3-flash", "args": ["--glm-pack", str(pack), "--max-context", "8192"],
        "env": {"MAYA_GLM_MTP_DECODE": "0", "MAYA_GLM_RESERVED_VRAM": "1", "MAYA_GLM_RAM_HEADROOM_GB": "16"},
        "private_key": "preserve-private"}))
    unit = tmp_path / "maya-glm53.service"
    unit.write_text(f"[Service]\nExecStart=/usr/bin/python3 -m serve.server --config {config.as_posix()}\n")
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"services": {"maya_glm": {"unit": unit.name, "scope": "user"}},
        "maya_glm_setup": {"config_file": str(config), "unit_file": str(unit), "maximum_context": 128000},
        "maya_setup": {"config_file": "/never/edit/ds41f.json"}, "unrelated": "keep"}))
    management = Management(str(settings), "", "", maya_glm_url="http://glm")
    state = {"running": True, "mtp": False, "calls": [], "fail_start": False}

    def live(_):
        context = int(json.loads(config.read_text())["args"][-1])
        return {"status": "ok", "model": "glm-5.3-flash", "max_context": context} if state["running"] else {"status": "unavailable"}

    def service(name, operation):
        assert name == "maya_glm"
        state["calls"].append(operation)
        if operation == "start" and state["fail_start"]:
            state["fail_start"] = False
            raise OperationsError("Startup failed", 502)
        state["running"] = operation == "start"
        if state["running"]:
            state["mtp"] = json.loads(config.read_text())["env"]["MAYA_GLM_MTP_DECODE"] == "1"
        return SimpleNamespace(stdout=b"", stderr=b"")

    management.service = service
    monkeypatch.setattr(maya_settings, "systemctl", lambda *args: f"FragmentPath={unit}\nDropInPaths=\nActiveState={'active' if state['running'] else 'inactive'}\nNeedDaemonReload=no\n")
    monkeypatch.setattr(maya_settings, "glm_inventory", live)
    monkeypatch.setattr(maya_settings, "glm_runtime_mode", lambda _: state["mtp"])
    monkeypatch.setattr(maya_settings, "get_json", lambda _: {"min": 4096, "max": 1048576, "context": live(None).get("max_context")})
    monkeypatch.setattr(maya_settings, "run_service", lambda m, spec: m.service(spec["backend"], spec["operation"]))
    return management, config, settings, state


def change(host, drafting=True):
    current = maya_config.snapshot(host[0], "maya_glm")
    assert current["configured"], current
    assert current["maximum_context"] == 128000
    return maya_config.review(host[0], {"revision": current["revision"], "context": 8192, "drafting": drafting}, "maya_glm")


def test_native_mtp_restarts_only_glm_and_preserves_memory_and_private_settings(host, tmp_path):
    reviewed = change(host)
    assert "preserve-private" not in json.dumps(reviewed)
    result = maya_config.run(host[0], {**reviewed, "backend": "maya_glm"}, tmp_path)
    saved = json.loads(host[1].read_text())
    assert result["drafting"] and host[3]["mtp"]
    assert host[3]["calls"] == ["stop", "start"]
    assert saved["private_key"] == "preserve-private"
    assert saved["env"]["MAYA_GLM_RESERVED_VRAM"] == "1"
    assert saved["env"]["MAYA_GLM_RAM_HEADROOM_GB"] == "16"
    assert saved["env"]["MAYA_GLM_MTP_DECODE"] == "1"
    assert "--dspark" not in saved["args"]
    assert json.loads(host[2].read_text())["maya_setup"]["config_file"] == "/never/edit/ds41f.json"


def test_stopped_mtp_edits_do_not_start_model(host, tmp_path):
    host[3]["running"] = False
    maya_config.run(host[0], {**change(host), "backend": "maya_glm"}, tmp_path)
    assert host[3]["calls"] == []
    assert json.loads(host[1].read_text())["env"]["MAYA_GLM_MTP_DECODE"] == "1"


def test_failed_glm_restart_restores_config_and_previous_mode(host, tmp_path):
    originals = host[1].read_bytes(), host[2].read_bytes()
    reviewed = change(host)
    host[3]["fail_start"] = True
    with pytest.raises(OperationsError, match="Startup failed"):
        maya_config.run(host[0], {**reviewed, "backend": "maya_glm"}, tmp_path)
    assert (host[1].read_bytes(), host[2].read_bytes()) == originals
    assert host[3]["running"] and not host[3]["mtp"]


def test_saved_mode_must_match_running_engine(host):
    host[3]["mtp"] = True
    state = maya_config.snapshot(host[0], "maya_glm")
    assert not state["configured"] and "Restart Maya GLM" in state["message"]


@pytest.mark.parametrize("key, value", [("MAYA_GLM_NO_MTP", "0"), ("MAYA_GLM_NO_SPEC", "0"), ("MAYA_GLM_MTP_DRAFT", "0"), ("MAYA_GLM_SEQS", "2")])
def test_enable_refuses_conflicting_flags_without_clearing_them(host, key, value):
    config = json.loads(host[1].read_text())
    config["env"][key] = value
    host[1].write_text(json.dumps(config))
    with pytest.raises(OperationsError, match="conflicts"):
        change(host)
    assert not change(host, drafting=False)["drafting"]
    assert json.loads(host[1].read_text())["env"][key] == value


def test_queue_binds_native_mode_to_review(host, monkeypatch):
    reviewed = change(host)
    from services.operations import jobs
    monkeypatch.setattr(jobs, "enqueue", lambda _, spec: spec)
    result = maya_config.enqueue_apply(host[0], reviewed, "maya_glm")
    assert result["backend"] == "maya_glm" and result["drafting"] and result["timeout"] == 1800
    with pytest.raises(OperationsError, match="exact"):
        maya_config.enqueue_apply(host[0], {**reviewed, "drafting": False}, "maya_glm")


@pytest.mark.parametrize("mode, deleted, expected", [("0", False, False), ("1", False, True), (None, False, None), ("1", True, None)])
def test_runtime_inspects_actual_child_environment_and_rejects_replaced_binary(tmp_path, monkeypatch, mode, deleted, expected):
    executable = tmp_path / "maya"
    executable.touch()
    task = Path("/proc/100/task/100")
    original_iterdir, original_read_text = Path.iterdir, Path.read_text
    original_read_bytes, original_readlink = Path.read_bytes, Path.readlink
    monkeypatch.setattr(maya_settings, "systemctl", lambda *_: "100\n")
    monkeypatch.setattr(Path, "iterdir", lambda p: iter([task]) if p.as_posix() == "/proc/100/task" else original_iterdir(p))
    monkeypatch.setattr(Path, "read_text", lambda p, *a, **kw: "200" if p.as_posix() == "/proc/100/task/100/children" else original_read_text(p, *a, **kw))
    raw = b"" if mode is None else f"STRATA_GLM_MTP_DECODE={mode}\0".encode()
    monkeypatch.setattr(Path, "read_bytes", lambda p: raw if p.as_posix() == "/proc/200/environ" else original_read_bytes(p))
    monkeypatch.setattr(Path, "readlink", lambda p: Path(str(executable) + (" (deleted)" if deleted else "")) if p.as_posix() == "/proc/200/exe" else original_readlink(p))
    profile = {"unit": "glm.service", "path": tmp_path / "glm.json", "config": {"exe": str(executable), "args": []}}
    if expected is None:
        with pytest.raises(OperationsError):
            maya_settings.glm_runtime_mode(profile)
    else:
        assert maya_settings.glm_runtime_mode(profile) is expected
