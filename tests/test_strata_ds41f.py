"""Isolated experimental deployment must not masquerade as Qwen or ready HTTP200."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from services.operations import strata_ds41f, exclusivity, benchmark_limits
from services.operations.management import Management
from services.operations.litellm_config import OperationsError


def test_readiness_requires_loaded_identity_and_consistent_context(monkeypatch):
    health = {"status": "ok", "loaded": True, "model": strata_ds41f.MODEL, "max_context": 8192}
    model = {"id": strata_ds41f.MODEL, "meta": {"n_ctx": 8192}, "status": {"value": "loaded"}}
    monkeypatch.setattr(strata_ds41f, "get_json", lambda url: health if url.endswith('/health') else {"data": [model]})
    assert strata_ds41f.inventory('http://native')["status"] == "ok"
    health["loaded"] = False
    assert strata_ds41f.inventory('http://native')["loaded"] is False
    health["loaded"] = True; model["meta"]["n_ctx"] = 4096
    assert strata_ds41f.inventory('http://native')["status"] == "degraded"
    model["meta"]["n_ctx"] = 8192; health["model"] = 'qwen'
    assert strata_ds41f.inventory('http://native')["status"] == "degraded"


def test_qualification_matches_binary_receipt_and_preserves_config(tmp_path):
    binary = tmp_path / "strata-dsv41"; binary.write_bytes(b"tested-binary")
    config = tmp_path / "config.json"; config.write_text(json.dumps({"exe": str(binary), "arch": "deepseek41", "model_name": strata_ds41f.MODEL, "args": ["--max-context", "8192"]}))
    receipt = tmp_path / "receipt.json"; receipt.write_text(json.dumps({"source_revision": strata_ds41f.REVISION, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "kernel_checks": ["ds4_iq_test", "ds4_tq_test", "ds4_tq_test_portable"]}))
    host = SimpleNamespace(settings={"strata_ds41f_setup": {"config_file": str(config), "build_receipt": str(receipt)}})
    original = config.read_bytes()
    assert strata_ds41f.qualification(host)["context"] == "8192"
    binary.write_bytes(b"replaced-binary")
    with pytest.raises(OperationsError, match="receipt"):
        strata_ds41f.qualification(host)
    assert config.read_bytes() == original


@pytest.mark.parametrize("other", ["maya", "maya_glm", "dwarfstar", "strata", "ollama"])
def test_manual_start_refuses_competing_models(other):
    host = SimpleNamespace(strata_ds41f_url='http://native', residency=lambda: {other: {"loaded": True}})
    with pytest.raises(OperationsError):
        exclusivity.ensure_available(host, "strata_ds41f")


def test_existing_engines_refuse_loaded_experimental_peer():
    host = SimpleNamespace(strata_ds41f_url='http://native', residency=lambda: {"strata_ds41f": {"loaded": True}})
    with pytest.raises(OperationsError, match="strata_ds41f"):
        exclusivity.ensure_available(host, "maya")


def test_distinct_url_and_service_are_required(tmp_path):
    file = tmp_path / "management.json"
    file.write_text(json.dumps({"services": {"strata": "qwen.service", "strata_ds41f": "qwen.service"}, "strata_ds41f_setup": {"url": "http://127.0.0.1:8095"}}))
    with pytest.raises(OperationsError, match="separate systemd"):
        Management(str(file), "http://127.0.0.1:8081", "")


def test_benchmark_budget_is_conservative_not_maya_tokenization(monkeypatch):
    monkeypatch.setattr(strata_ds41f, "inventory", lambda url: {"status": "ok", "loaded": True, "aliases": [strata_ds41f.MODEL], "model": strata_ds41f.MODEL, "max_context": 8192})
    workload = {"tasks": [{"prompt": "test"}], "warmup_task": {"prompt": "warm"}, "task_seed": 1}
    result = benchmark_limits.limits(SimpleNamespace(strata_ds41f_url='http://native'), {"backend": "strata_ds41f", "model": strata_ds41f.MODEL}, workload)
    assert result["exact"] is False and result["prompt_tokens"] == 2052
    assert result["max_output_tokens"] == 6140


def test_router_rejects_missing_qualification_before_eviction(monkeypatch):
    from services.operations.inference_backends import NativeEngines
    calls = []
    def unqualified(host):
        raise OperationsError("Missing build receipt", 409)
    monkeypatch.setattr(strata_ds41f, "qualification", unqualified)
    engine = NativeEngines(SimpleNamespace())
    engine.release = lambda *args: calls.append(args)
    with pytest.raises(OperationsError, match="receipt"):
        engine.prepare("strata_ds41f", strata_ds41f.MODEL)
    assert calls == []


def test_router_keeps_configured_peer_protected_without_public_route():
    from services.operations.inference_router import validate_configuration
    host = SimpleNamespace(settings={"services": {"maya": "maya.service"}}, maya_url="http://127.0.0.1:8092", strata_ds41f_url="http://127.0.0.1:8095")
    configured, routes = validate_configuration({"backends": {"maya": host.maya_url}, "routes": {"m": {"backend": "maya", "model": "ds"}}}, host)
    assert configured.strata_ds41f_url == host.strata_ds41f_url


def test_artifact_manifest_has_only_selected_immutable_sources():
    from pathlib import Path
    manifest = json.loads((Path(__file__).resolve().parents[1] / "scripts/strata-ds41f-artifacts.json").read_text())
    assert len(manifest["files"]) == 7
    assert sum(row["bytes"] for row in manifest["files"]) == 256816878701
    assert all(len(row["revision"]) == 40 and len(row["sha256"]) == 64 for row in manifest["files"])
    assert sum("IQ2_XXS-" in row["path"] for row in manifest["files"]) == 4
