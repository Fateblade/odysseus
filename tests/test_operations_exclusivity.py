"""Exclusive engines refuse overlapping model loads without unloading anything."""

from types import SimpleNamespace

import pytest

from services.operations.exclusivity import EXCLUSIVE, ensure_available
from services.operations.litellm_config import OperationsError
from services.operations.management import Management


@pytest.mark.parametrize("backend", ["maya", "maya_glm", "dwarfstar", "strata", "ollama"])
@pytest.mark.parametrize("loaded", EXCLUSIVE)
def test_exclusive_resident_blocks_other_loads(backend, loaded):
    if backend == loaded:
        return
    host = SimpleNamespace(maya_url="http://maya", residency=lambda: {loaded: {"loaded": True}},
                           service=lambda *_: pytest.fail("No service may be stopped automatically"))
    with pytest.raises(OperationsError, match="Stop"):
        ensure_available(host, backend)


@pytest.mark.parametrize("backend", EXCLUSIVE)
@pytest.mark.parametrize("loaded", ["strata", "ollama"])
def test_shared_resident_blocks_exclusive_start(backend, loaded):
    with pytest.raises(OperationsError, match="Unload competing"):
        ensure_available(SimpleNamespace(residency=lambda: {loaded: {"loaded": True}}), backend)


def test_shared_engines_can_coexist_without_an_exclusive_model():
    host = SimpleNamespace(maya_url="http://maya", residency=lambda: {
        "maya": {"status": "stopped", "configured": True}, "ollama": {"loaded": ["model"]}, "strata": {"loaded": True}})
    ensure_available(host, "ollama")
    ensure_available(host, "strata")


def test_unreachable_exclusive_requires_confirmed_inactive_service():
    host = SimpleNamespace(maya_url="http://maya", residency=lambda: {"maya": {"status": "unavailable", "configured": True}},
                           service=lambda *args, **kw: SimpleNamespace(returncode=0, stdout=b"active\n"))
    with pytest.raises(OperationsError, match="not confirmed stopped"):
        ensure_available(host, "ollama")
    host.service = lambda *args, **kw: SimpleNamespace(returncode=3, stdout=b"inactive\n")
    ensure_available(host, "ollama")


def test_dwarfstar_actions_use_the_same_host_queue(tmp_path, monkeypatch):
    import json
    from services.operations import jobs
    path = tmp_path / "management.json"
    path.write_text(json.dumps({"services": {"dwarfstar": "dwarfstar.service"}}))
    host = Management(str(path), "", "", dwarfstar_url="http://dwarfstar")
    monkeypatch.setattr(jobs, "enqueue", lambda _, spec: spec)
    result = host.execute({"target": "service", "name": "dwarfstar", "operation": "stop"})
    assert result == {"kind": "inference_service", "backend": "dwarfstar", "operation": "stop", "timeout": 900}
