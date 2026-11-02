"""Metadata restrictions, route guarantees and reviewed publication contracts."""

import copy
import json
import time
from types import SimpleNamespace

import pytest

from services.operations import model_metadata_store as store
from services.operations import model_metadata_sources as sources
from services.operations import litellm_host as host
from services.operations.litellm_config import OperationsError, candidate, snapshot
from services.operations.model_metadata import envelope, resolve, route_envelopes

CONFIG = b"""model_list:
  - model_name: coding
    litellm_params:
      model: openai/native
      api_base: http://127.0.0.1:8094/v1
      api_key: private-key
    model_info:
      mode: chat
      custom_setting: retain-me
router_settings:
  model_group_alias:
    pa-coder: coding
"""


def loaded(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes(CONFIG)
    result = store.execute(path, "gateway", "http://gateway", {"action": "snapshot"}, SimpleNamespace())
    return path, result


def payload(result, action):
    return {"action": action, "revision": result["revision"], "metadata_revision": result["metadata_revision"],
            "draft": copy.deepcopy(result["draft"]), "_audit": {"actor": "test-admin"}}


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "4096", 2**53])
def test_invalid_integer(value):
    with pytest.raises(OperationsError):
        resolve({}, {"context_window": {"operation": "set", "value": value}})


def test_restrictions_unknowns_reset_and_runtime_reduction():
    detected = {"context_window": 65536, "supports_function_calling": False}
    overrides = {"context_window": {"operation": "set", "value": 32768}}
    assert resolve(detected, overrides)["effective"]["context_window"] == 32768
    assert resolve(detected, {})["effective"]["context_window"] == 65536
    assert resolve(detected, {"context_window": {"operation": "suppress"}})["effective"]["context_window"] is None
    with pytest.raises(OperationsError):
        resolve(detected, {"supports_function_calling": {"operation": "set", "value": True}})
    reduced = resolve({"context_window": 8192}, overrides, strict=False)
    assert reduced["effective"]["context_window"] == 8192
    assert reduced["conflicts"] == ["context_window"]
    assert resolve({}, {"supports_vision": {"operation": "set", "value": False}})["effective"]["supports_vision"] is False


def test_context_maxima_are_independent():
    result = resolve({}, {field: {"operation": "set", "value": value} for field, value in
                          (("context_window", 100), ("max_input_tokens", 90), ("max_output_tokens", 80))})
    assert result["effective"]["max_output_tokens"] == 80


def test_envelope_requires_all_routes_and_preserves_false():
    assert envelope([{"context_window": 100, "supports_vision": True}, {}])["context_window"] is None
    assert envelope([{"supports_vision": False}, {}])["supports_vision"] is False
    assert envelope([{"mode": "chat"}, {"mode": "embedding"}])["mode"] is None


def test_specialized_fallbacks_are_included_without_mutating_yaml():
    config = {"model_list": [{"model_name": name} for name in ("a", "b", "c")],
              "router_settings": {"fallbacks": [{"a": ["b"]}], "context_window_fallbacks": [{"a": ["c"]}]}}
    original = copy.deepcopy(config)
    result = route_envelopes(config, [{"context_window": value} for value in (100, 80, 40)])
    assert result["a"]["context_window"] == 40
    assert config == original


def test_review_apply_persistence_and_safe_projection(tmp_path, monkeypatch):
    path, initial = loaded(tmp_path)
    request = payload(initial, "preview")
    request["draft"]["models"][0]["metadata"]["overrides"].update({
        "context_window": {"operation": "set", "value": 32768},
        "supports_function_calling": {"operation": "set", "value": False}})
    reviewed = store.execute(path, "gateway", "http://gateway", request, None)
    assert reviewed["metadata_public"]["pa-coder"]["context_window"] == 32768
    assert "private-key" not in json.dumps(reviewed)
    assert not store.location(path).exists()
    monkeypatch.setattr(host, "docker_command", lambda *args: b"")
    monkeypatch.setattr(host, "await_gateway", lambda *args: None)
    monkeypatch.setattr(host, "verify_metadata", lambda *args: None)
    applied = store.execute(path, "gateway", "http://gateway", {**request, "action": "apply", "review_digest": reviewed["review_digest"]}, None)
    assert applied["draft"]["models"][0]["metadata"]["overrides"]["context_window"]["value"] == 32768
    assert b"private-key" in path.read_bytes()
    assert b"custom_setting: retain-me" in path.read_bytes()
    assert "private-key" not in store.location(path).read_text()
    reloaded = store.execute(path, "gateway", "http://gateway", {"action": "snapshot"}, None)
    assert reloaded["metadata_public"]["coding"]["supports_function_calling"] is False
    with pytest.raises(OperationsError, match="changed"):
        store.execute(path, "gateway", "http://gateway", request, None)


def test_failed_metadata_verification_rolls_back_yaml_and_sidecar(tmp_path, monkeypatch):
    path, initial = loaded(tmp_path)
    request = payload(initial, "preview")
    reviewed = store.execute(path, "gateway", "http://gateway", request, None)
    monkeypatch.setattr(host, "docker_command", lambda *args: b"")
    monkeypatch.setattr(host, "await_gateway", lambda *args: None)
    def fail(*args):
        raise OperationsError("metadata mismatch")
    monkeypatch.setattr(host, "verify_metadata", fail)
    with pytest.raises(OperationsError, match="recovery was verified"):
        store.execute(path, "gateway", "http://gateway", {**request, "action": "apply", "review_digest": reviewed["review_digest"]}, None)
    assert path.read_bytes() == CONFIG
    assert not store.location(path).exists()


def test_fallback_envelope_and_alias(tmp_path):
    _, initial = loaded(tmp_path)
    draft = initial["draft"]
    draft["models"][0]["metadata"]["overrides"]["context_window"] = {"operation": "set", "value": 65536}
    draft["models"].append({"index": None, "model_name": "fallback", "model": "openai/other",
                             "api_base": "https://remote/v1", "metadata": {"overrides": {}}})
    draft["fallbacks"] = [{"coding": ["fallback"]}]
    state = store.import_existing(CONFIG, {"schema_version": 1, "sources": {}, "deployments": {}})
    _, config = candidate(CONFIG, draft, metadata_state=state)
    assert config["model_list"][0]["model_info"]["odysseus"]["context_window"] is None


def test_discovery_uses_configured_router_and_never_loads(monkeypatch):
    from services.operations import overview
    management = SimpleNamespace(settings={}, ollama_url="http://native:11434")
    monkeypatch.setattr(sources, "router_configuration", lambda _: {
        "port": 8094, "routes": {"local-embed": {"backend": "ollama", "model": "embed:latest"}}})
    monkeypatch.setattr(overview, "ollama", lambda _: {"status": "ok", "installed": [{"name": "embed:latest", "digest": "abc"}],
                                                    "loaded": [{"name": "embed:latest", "context_length": 4096}]})
    monkeypatch.setattr(sources, "show_ollama", lambda *_: {"capabilities": ["embedding"], "model_info": {"qwen.context_length": 32768}})
    catalog, _ = sources.collect(management)
    entry = next(iter(catalog.values()))
    assert entry["model"] == "openai/local-embed"
    assert entry["api_base"] == "http://127.0.0.1:8094/v1"
    assert entry["detected"] == {"mode": "embedding", "context_window": 4096}
    assert entry["details"]["published_context"] == 32768


def test_refresh_invalidates_old_review_and_retains_failed_observation_time(tmp_path, monkeypatch):
    path, initial = loaded(tmp_path)
    original = {"id": "old", "status": "fresh", "detected": {"context_window": 100}, "last_success_at": 123}
    state = store.import_existing(CONFIG, store.read(store.location(path)))
    state["sources"] = {"old": original}
    from services.operations.jobs import write_json
    write_json(store.location(path), state)
    current = store.execute(path, "gateway", "http://gateway", {"action": "snapshot"}, None)
    monkeypatch.setattr(sources, "collect", lambda _: ({}, []))
    refreshed = store.execute(path, "gateway", "http://gateway", payload(current, "metadata_refresh"), None)
    old = refreshed["metadata_sources"][0]
    assert old["last_success_at"] == 123
    assert old["detected"] == {}
    with pytest.raises(OperationsError, match="changed"):
        store.execute(path, "gateway", "http://gateway", payload(current, "preview"), None)


def test_credential_reference_and_secret_retention():
    draft = snapshot(CONFIG)["draft"]
    draft["models"][0]["credential_reference"] = "os.environ/REMOTE_API_KEY"
    _, config = candidate(CONFIG, draft)
    assert config["model_list"][0]["litellm_params"]["api_key"] == "os.environ/REMOTE_API_KEY"
    draft["models"][0]["credential_reference"] = "actual-secret"
    with pytest.raises(OperationsError, match="environment variable"):
        candidate(CONFIG, draft)


def test_browser_cannot_forge_observations_or_bypass_known_local_ceiling(tmp_path):
    path, _ = loaded(tmp_path)
    state = store.import_existing(CONFIG, store.read(store.location(path)))
    state["sources"]["native"] = {"id": "native", "model": "openai/native", "api_base": "http://127.0.0.1:8094/v1",
                                   "detected": {"context_window": 4096}, "status": "fresh", "last_success_at": time.time()}
    draft = store.decorate(CONFIG, state)["draft"]
    metadata = draft["models"][0]["metadata"]
    metadata["detected"] = {"context_window": 999999}
    metadata["overrides"]["context_window"] = {"operation": "set", "value": 8192}
    with pytest.raises(OperationsError, match="detected supported"):
        candidate(CONFIG, draft, metadata_state=state)
    metadata["source_id"] = "forged"
    with pytest.raises(OperationsError, match="selection changed"):
        candidate(CONFIG, draft, metadata_state=state)


def test_identity_replacement_requires_reset_and_stale_source_blocks_apply(tmp_path):
    path, initial = loaded(tmp_path)
    state = store.import_existing(CONFIG, store.read(store.location(path)))
    draft = initial["draft"]
    draft["models"][0]["model"] = "openai/replacement"
    with pytest.raises(OperationsError, match="identity changed"):
        candidate(CONFIG, draft, metadata_state=state)
    draft["models"][0]["metadata"]["reset_identity"] = True
    candidate(CONFIG, draft, metadata_state=state)
    state["sources"]["expired"] = {"model": "openai/replacement", "api_base": "http://127.0.0.1:8094/v1",
                                    "status": "fresh", "last_success_at": time.time() - 901, "detected": {}}
    draft["models"][0]["metadata"]["source_id"] = "expired"
    with pytest.raises(OperationsError, match="stale"):
        candidate(CONFIG, draft, metadata_state=state)


def test_runtime_invalidation_retains_history_and_discards_current_observations(tmp_path):
    path, _ = loaded(tmp_path)
    state = store.import_existing(CONFIG, store.read(store.location(path)))
    state["sources"]["native"] = {"status": "fresh", "last_success_at": 123, "detected": {"context_window": 4096}}
    from services.operations.jobs import write_json
    write_json(store.location(path), state)
    store.invalidate(path)
    updated = store.read(store.location(path))["sources"]["native"]
    assert updated["status"] == "stale"
    assert updated["detected"] == {}
    assert updated["last_detected"] == {"context_window": 4096}
    assert updated["last_success_at"] == 123


def test_gateway_verification_matches_deployment_and_false(monkeypatch):
    state = store.import_existing(CONFIG, {"schema_version": 1, "sources": {}, "deployments": {}})
    draft = store.decorate(CONFIG, state)["draft"]
    _, config = candidate(CONFIG, draft, metadata_state=state)
    row = config["model_list"][0]
    monkeypatch.setattr(host, "get_json", lambda *args: {"data": [copy.deepcopy(row)]})
    host.verify_metadata("http://gateway", config, "gateway")
    broken = copy.deepcopy(row)
    broken["model_info"]["odysseus"]["context_window"] = 999
    monkeypatch.setattr(host, "get_json", lambda *args: {"data": [broken]})
    with pytest.raises(OperationsError):
        host.verify_metadata("http://gateway", config, "gateway")
