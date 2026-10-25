"""Keep-alive edits preserve endpoint/model identity and reject stale reviews."""

import json
from types import SimpleNamespace

import pytest

from services.operations.inference_settings import snapshot, update
from services.operations.litellm_config import OperationsError


def test_update_changes_only_retention_and_preserves_previous_copy(tmp_path):
    path = tmp_path / "inference-router.json"
    value = {"private": "preserve", "backends": {"maya": "http://127.0.0.1:8092"}, "routes": {"ds": {"backend": "maya", "model": "ds", "keep_alive_seconds": 600}}}
    path.write_text(json.dumps(value))
    host = SimpleNamespace(path=tmp_path / "management.json", settings={})
    before = snapshot(host)
    result = update(host, {"revision": before["revision"], "keep_alive_seconds": {"ds": 1200}})
    assert result["routes"][0]["keep_alive_seconds"] == 1200
    saved = json.loads(path.read_text())
    assert saved["private"] == "preserve" and saved["backends"] == value["backends"]
    assert saved["routes"]["ds"]["model"] == "ds"
    assert json.loads(path.with_name(path.name + ".before-keep-alive").read_text()) == value
    with pytest.raises(OperationsError, match="changed"):
        update(host, {"revision": before["revision"], "keep_alive_seconds": {"ds": 0}})


def test_uninstalled_router_does_not_break_management(tmp_path):
    host = SimpleNamespace(path=tmp_path / "management.json", settings={})
    assert snapshot(host)["installed"] is False


@pytest.mark.parametrize("ttl", [True, -1, float('inf'), "10m"])
def test_invalid_duration_does_not_modify_file(tmp_path, ttl):
    path = tmp_path / "inference-router.json"
    path.write_text(json.dumps({"routes": {"ds": {"backend": "maya", "model": "ds"}}}))
    original = path.read_bytes()
    host = SimpleNamespace(path=tmp_path / "management.json", settings={})
    with pytest.raises(OperationsError):
        update(host, {"revision": snapshot(host)["revision"], "keep_alive_seconds": {"ds": ttl}})
    assert path.read_bytes() == original
