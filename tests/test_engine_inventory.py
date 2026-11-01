import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("inventory", Path(__file__).resolve().parents[1] / "scripts/odysseus-engine-inventory.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


def test_origin_removes_credentials_and_query_tokens():
    assert inventory.origin("https://user:secret@example.com/repo.git?token=secret") == "https://example.com/repo.git"
    assert inventory.origin("git@github.com:owner/repo.git") == "github.com:owner/repo.git"


def test_only_approved_process_tuning_values_are_returned(monkeypatch):
    monkeypatch.setattr(inventory.os, "readlink", lambda path: "/engine (deleted)")
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"API_KEY=private-token\0OLLAMA_KV_CACHE_TYPE=q8_0\0")
    result = inventory.process(12)
    assert result["replaced_binary"]
    assert result["settings"] == {"OLLAMA_KV_CACHE_TYPE": "q8_0"}
    assert "private-token" not in json.dumps(result)


@pytest.mark.parametrize("url", ["https://example.com", "http://user:secret@localhost", "http://localhost/foo"])
def test_inventory_refuses_nonlocal_or_credentialed_endpoints(url):
    with pytest.raises(ValueError):
        inventory.ollama_version(url)
