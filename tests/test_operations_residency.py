"""Live residency distinguishes empty inventories from unavailable backends."""

from services.operations import overview
from services.operations.management import Management


def test_loaded_ollama_models_include_runtime_context_memory_and_expiry(monkeypatch):
    def respond(url):
        if url.endswith('/api/tags'):
            return {"models": [{"name": "qwen3-coder:30b", "size": 19_000_000_000}]}
        assert url.endswith('/api/ps')
        return {"models": [{"name": "qwen3-coder:30b", "context_length": 65536,
                            "size": 23_000_000_000, "size_vram": 22_000_000_000,
                            "expires_at": "2026-10-20T18:00:00Z"}]}

    monkeypatch.setattr(overview, "get_json", respond)
    result = overview.ollama("http://ollama")
    assert result["loaded_available"] is True
    loaded = result["loaded"][0]
    assert loaded["context_length"] == 65536
    assert loaded["size_bytes"] == 23_000_000_000
    assert loaded["gpu_bytes"] == 22_000_000_000
    assert loaded["expires_at"] == "2026-10-20T18:00:00Z"
    assert result["installed"][0]["size_bytes"] == 19_000_000_000


def test_empty_and_unavailable_ollama_are_distinct(monkeypatch):
    monkeypatch.setattr(overview, "get_json", lambda url: {"models": []})
    empty = overview.ollama("http://ollama")
    assert empty["loaded_available"] is True
    assert empty["loaded"] == []

    def unavailable(url):
        if url.endswith('/api/ps'):
            raise OSError("offline")
        return {"models": []}

    monkeypatch.setattr(overview, "get_json", unavailable)
    failed = overview.ollama("http://ollama")
    assert failed["loaded_available"] is False
    assert failed["installed_available"] is True
    assert failed["status"] == "degraded"


def test_residency_reports_unloaded_strata_without_loading_models(monkeypatch):
    requested = []

    def respond(url):
        requested.append(url)
        if url.endswith('/health'):
            return {"status": "ok", "loaded": False, "model": "qwen-flash", "max_context": 131072}
        if url.endswith('/metrics'):
            return {"live": {"state": "idle", "queued": 0}}
        return {"models": []}

    monkeypatch.setattr(overview, "get_json", respond)
    result = Management(None, "http://strata", "http://ollama").residency()
    assert result["strata"]["loaded"] is False
    assert result["strata"]["max_context"] == 131072
    assert result["sampled_at"] > 0
    assert set(requested) == {"http://strata/health", "http://strata/metrics", "http://strata/api/requests", "http://ollama/api/tags", "http://ollama/api/ps"}


def test_unreachable_strata_does_not_claim_unloaded(monkeypatch):
    def unavailable(url):
        raise OSError("offline")

    monkeypatch.setattr(overview, "get_json", unavailable)
    result = Management(None, "http://strata", "http://ollama").residency()
    assert result["strata"]["status"] == "unavailable"
    assert "loaded" not in result["strata"]
    assert result["ollama"]["loaded_available"] is False
