"""File-backed gateway changes preserve secrets and recover failed restarts."""

import copy
import json

import pytest

from services.operations import litellm_host as host
from services.operations.litellm_config import (
    OperationsError, candidate, review, revision, snapshot,
)


CONFIG = b"""# Existing deployment
general_settings:
  master_key: secret-admin-key
model_list:
  - model_name: pa-primary
    litellm_params:
      model: ollama_chat/qwen36-8k:latest
      api_base: http://127.0.0.1:11434
      api_key: secret-provider-key
      timeout: 90
    model_info:
      mode: chat
  - model_name: pa-utility
    litellm_params:
      model: ollama_chat/granite4.1:8b
      api_base: http://127.0.0.1:11434
router_settings:
  num_retries: 2
"""


def strata_draft():
    draft = copy.deepcopy(snapshot(CONFIG)["draft"])
    draft["models"].append({"index": None, "model_name": "strata", "model": "openai/qwen3.8-flash-next-iq2_xs",
                            "api_base": "http://127.0.0.1:8081/v1"})
    draft["aliases"] = {"pa-strata": "strata"}
    draft["fallbacks"] = [{"pa-primary": ["strata", "pa-utility"]}]
    return draft


def test_review_and_edit_preserve_credentials_comments_and_unknown_settings():
    draft = strata_draft()
    edited, config = candidate(CONFIG, draft)
    assert edited.startswith(b"# Existing deployment")
    assert config["general_settings"]["master_key"] == "secret-admin-key"
    assert config["model_list"][0]["litellm_params"]["api_key"] == "secret-provider-key"
    assert config["model_list"][0]["litellm_params"]["timeout"] == 90
    assert config["model_list"][0]["model_info"]["mode"] == "chat"
    assert config["router_settings"]["num_retries"] == 2
    public = json.dumps({"snapshot": snapshot(CONFIG), "review": review(CONFIG, draft)})
    assert "secret-admin-key" not in public
    assert "secret-provider-key" not in public
    assert review(CONFIG, draft)["changed"] is True


def test_apply_requires_current_revision_and_exact_review(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes(CONFIG)
    payload = {"action": "apply", "revision": "stale", "draft": strata_draft(), "review_digest": "wrong"}
    with pytest.raises(OperationsError) as error:
        host.execute(path, "litellm", "http://127.0.0.1:4000", payload)
    assert error.value.status == 409
    payload["revision"] = revision(CONFIG)
    with pytest.raises(OperationsError) as error:
        host.execute(path, "litellm", "http://127.0.0.1:4000", payload)
    assert error.value.status == 409
    assert path.read_bytes() == CONFIG


def test_failed_health_check_restores_previous_yaml_and_verifies_recovery(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_bytes(CONFIG)
    configs, restarts = [], []
    monkeypatch.setattr(host, "docker_command", lambda *args: restarts.append(args))
    def health(_url, config, _container):
        configs.append(config)
        if len(configs) == 1:
            raise OperationsError("unhealthy", 502)
    monkeypatch.setattr(host, "await_gateway", health)
    draft = strata_draft()
    with pytest.raises(OperationsError, match="recovery was verified"):
        host.execute(path, "litellm", "http://127.0.0.1:4000", {
            "action": "apply", "revision": revision(CONFIG), "draft": draft,
            "review_digest": review(CONFIG, draft)["review_digest"],
        })
    assert path.read_bytes() == CONFIG
    assert len(restarts) == 2
    assert len(configs[-1]["model_list"]) == 2
