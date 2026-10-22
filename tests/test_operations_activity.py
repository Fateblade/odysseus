"""Activity samples tolerate PID reuse and expose metrics without request payloads."""

from services.operations import activity, overview
from services.operations.management import Management


def process(ticks=0, identity="old", engine=0):
    return {"pid": 42, "name": "ollama", "state": "R", "unit": "ollama.service", "threads": 8,
            "cpu_ticks": ticks, "identity": identity, "rss_pages": 100,
            "gpu_clients": {"gpu:1": {"engines": {"drm-engine-compute": engine}, "vram_bytes": 1024}}}


def test_process_usage_uses_interval_and_rejects_reused_pids():
    first = {42: process(10, engine=1_000_000_000)}
    second = {42: process(110, engine=1_250_000_000)}
    row = activity.process_usage(first, second, 0.5, 100, 4096)[0]
    assert row["cpu_percent"] == 200
    assert row["gpu_engine_percent"] == 50
    assert row["rss_bytes"] == 409600
    assert row["gpu_vram_bytes"] == 1024
    second[42]["identity"] = "new"
    row = activity.process_usage(first, second, 0.5, 100, 4096)[0]
    assert row["cpu_percent"] is None
    assert row["gpu_engine_percent"] is None


def test_gpu_client_file_descriptors_are_deduplicated(tmp_path, monkeypatch):
    (tmp_path / "fd").mkdir()
    (tmp_path / "fdinfo").mkdir()
    for fd in ("3", "4"):
        (tmp_path / "fd" / fd).touch()
        (tmp_path / "fdinfo" / fd).write_text("drm-driver: amdgpu\ndrm-pdev: 0000:01:00.0\ndrm-client-id: 7\ndrm-engine-compute: 123 ns\ndrm-memory-vram: 8 KiB\n")
    monkeypatch.setattr(activity.os, "readlink", lambda path: "/dev/dri/renderD128")
    clients = activity.gpu_clients(tmp_path)
    assert len(clients) == 1
    assert next(iter(clients.values()))["vram_bytes"] == 8192


def test_proc_parser_handles_spaces_in_names_and_disappearing_processes(tmp_path):
    directory = tmp_path / "42"
    directory.mkdir()
    fields = ["0"] * 22
    fields[0], fields[11], fields[12], fields[17], fields[19], fields[21] = "R", "10", "20", "8", "1234", "100"
    (directory / "stat").write_text("42 (engine (worker)) " + " ".join(fields))
    (directory / "cgroup").write_text("0::/system.slice/ollama.service\n")
    (tmp_path / "43").mkdir()
    result, skipped = activity.process_snapshot(tmp_path)
    assert result[42]["name"] == "engine (worker)"
    assert result[42]["cpu_ticks"] == 30
    assert result[42]["unit"] == "ollama.service"
    assert result[42]["identity"] == "1234"
    assert skipped == 1


def test_strata_activity_omits_payloads_and_tolerates_missing_request_monitor(monkeypatch):
    def respond(url):
        if url.endswith('/health'):
            return {"status": "ok", "loaded": True}
        if url.endswith('/metrics'):
            return {"live": {"state": "generating", "generated": 12, "elapsed_s": 2},
                    "requests": [{"output_tokens": 50, "prompt_tokens": 100, "prompt": "private"}]}
        return {"requests": [{"id": "abc", "state": "generating", "input": "secret prompt",
                              "output": "private output", "reasoning": "private reasoning"}]}

    monkeypatch.setattr(overview, "get_json", respond)
    result = overview.strata("http://strata")
    assert result["activity"]["generated"] == 12
    assert result["requests"][0]["id"] == "abc"
    assert "input" not in result["requests"][0]
    assert "output" not in result["requests"][0]
    assert "reasoning" not in result["requests"][0]
    assert "prompt" not in result["recent_requests"][0]

    def legacy(url):
        if url.endswith('/api/requests'):
            raise OSError("not supported")
        return respond(url)

    monkeypatch.setattr(overview, "get_json", legacy)
    result = overview.strata("http://strata")
    assert result["requests_available"] is False
    assert result["metrics_available"] is True
    assert result["activity"]["state"] == "generating"


def test_gateway_activity_never_returns_credentials(tmp_path, monkeypatch):
    from services.operations import litellm_host
    path = tmp_path / "config.yaml"
    path.write_text("model_list: []\ngeneral_settings:\n  master_key: secret-key\n")
    def respond(url, key):
        assert key == "secret-key"
        assert url.endswith('/health/backlog')
        return {"in_flight_requests": 3, "extra": "secret-key"}

    monkeypatch.setattr(litellm_host, "get_json", respond)
    result = activity.gateway_activity("http://gateway", str(path), "litellm")
    assert result == {"status": "ok", "in_flight_requests": 3}
    monkeypatch.setattr(litellm_host, "get_json", lambda *args: {"in_flight_requests": "unknown"})
    assert activity.gateway_activity("http://gateway", str(path), "litellm")["status"] == "unavailable"


def test_missing_ollama_target_is_visible_without_granting_control():
    result = Management(None, "http://strata", "http://ollama").catalog()
    assert "ollama" in result["unconfigured_services"]
    assert "ollama" not in result["services"]
