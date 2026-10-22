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


def test_process_docker_scope_includes_identity_and_executable(tmp_path, monkeypatch):
    container_id = '09e2b22b36fa' + 'a' * 52
    process = tmp_path / '86699'
    process.mkdir()
    fields = ['0'] * 22
    fields[0], fields[1], fields[17], fields[19], fields[21] = 'S', '86600', '166', '1234', '100'
    (process / 'stat').write_text('86699 (uvicorn) ' + ' '.join(fields))
    (process / 'cgroup').write_text(f'0::/system.slice/docker-{container_id}.scope\n')
    monkeypatch.setattr(activity.os, 'readlink', lambda path: '/usr/local/bin/python3.11')
    rows, _ = activity.process_snapshot(tmp_path)
    sampled = activity.process_usage(rows, rows, 1, 100, 4096)
    assert sampled[0]['container_id'] == container_id
    assert sampled[0]['executable'] == '/usr/local/bin/python3.11'
    assert sampled[0]['parent_pid'] == 86600
    (process / 'cgroup').write_text(f'12:memory:/docker/{container_id}\n')
    legacy, _ = activity.process_snapshot(tmp_path)
    assert legacy[86699]['container_id'] == container_id
    result = activity.associate_containers({'items': sampled}, {'status': 'ok', 'items': [
        {'id': container_id, 'name': 'odysseus-fateblade-odysseus-1', 'image': 'odysseus-fateblade:dwarfstar-poc'}]})
    assert result['items'][0]['container']['name'] == 'odysseus-fateblade-odysseus-1'


def test_container_lookup_is_independent_of_process_name_and_handles_missing_inventory():
    container_id = 'a' * 64
    for name in ('uvicorn', 'python', 'worker'):
        process = {'name': name, 'container_id': container_id}
        activity.associate_containers({'items': [process]}, {'status': 'ok', 'items': [
            {'id': container_id, 'name': 'gateway', 'image': 'gateway:image'}]})
        assert process['container']['name'] == 'gateway'
    process = {'container_id': container_id}
    activity.associate_containers({'items': [process]}, {'status': 'unavailable'})
    assert 'container' not in process
    activity.associate_containers({'items': [process]}, {'status': 'ok', 'items': [
        {'id': 'a' * 12, 'name': 'one'}, {'id': 'a' * 13, 'name': 'two'}]})
    assert 'container' not in process


def test_inventory_returns_full_ids_without_inspecting_container_secrets(monkeypatch):
    import json
    from types import SimpleNamespace
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps({'ID': 'a' * 64, 'Names': 'odysseus', 'Image': 'image:tag'}))
    monkeypatch.setattr(overview.subprocess, 'run', run)
    result = overview.containers()
    assert result['items'][0]['id'] == 'a' * 64
    assert '--no-trunc' in calls[0]
    assert 'inspect' not in calls[0]


def test_memory_reports_kernel_and_cache_counters_without_changing_used_formula(monkeypatch):
    monkeypatch.setattr(overview, 'read_text', lambda path: '''MemTotal: 10000 kB
MemAvailable: 4500 kB
MemFree: 1000 kB
AnonPages: 2000 kB
Cached: 4000 kB
Buffers: 100 kB
Shmem: 500 kB
Slab: 800 kB
SReclaimable: 600 kB
KernelStack: 30 kB
PageTables: 40 kB
SwapTotal: 2000 kB
SwapFree: 1800 kB
''')
    result = overview.memory()
    assert result['used_bytes'] == 5500 * 1024
    assert result['cached_bytes'] == 4000 * 1024
    assert result['shared_bytes'] == 500 * 1024
    assert result['slab_bytes'] == 800 * 1024
    assert result['slab_reclaimable_bytes'] == 600 * 1024
    assert result['kernel_stack_bytes'] == 30 * 1024
    assert result['page_tables_bytes'] == 40 * 1024
    monkeypatch.setattr(overview, 'read_text', lambda path: None)
    assert all(value is None for value in overview.memory().values())


def test_process_candidates_preserve_top_thirty_memory_consumers():
    processes = {pid: {**process(), 'rss_pages': pid, 'pid': pid} for pid in range(1, 101)}
    rows = activity.process_usage(processes, processes, 1, 100, 4096)
    assert set(range(71, 101)).issubset({row['pid'] for row in rows})
