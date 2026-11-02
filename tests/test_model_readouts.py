import json
from types import SimpleNamespace

from services.operations.model_readouts import decorate, latest


def save(base, name, backend, model, time, decode=None, end=12, state="completed"):
    folder = base / name; folder.mkdir()
    (folder / "job.json").write_text(json.dumps({"id": name, "created_at": time, "state": state,
        "specification": {"kind": "benchmark", "backend": backend, "model": model},
        "result": {"summary": {"decode_tps": decode, "end_to_end_tps": end}, "samples": [], "prompt": "private"}}))


def test_latest_uses_backend_and_exact_model_not_highest_rate(tmp_path):
    host = SimpleNamespace(settings={"jobs_directory": str(tmp_path)}, path=None)
    save(tmp_path, "first", "ollama", "model:8b", 1, decode=90)
    save(tmp_path, "latest", "ollama", "model:8b", 2, decode=20)
    save(tmp_path, "other", "maya", "model:8b", 3, decode=50)
    save(tmp_path, "suffix", "ollama", "model:8b-extra", 4, decode=77)
    data = decorate(host, {"ollama": {"loaded": [{"name": "model:8b"}, {"name": "unknown"}], "installed": []}})
    rows = data["ollama"]["loaded"]
    assert rows[0]["last_measurement"]["value"] == 20
    assert rows[1]["last_measurement"] is None
    assert "private" not in json.dumps(data)


def test_latest_falls_back_to_end_to_end_and_is_not_page_limited(tmp_path):
    host = SimpleNamespace(settings={"jobs_directory": str(tmp_path)})
    save(tmp_path, "old", "dwarfstar", "ds", 1, end=15.4)
    for i in range(101):
        save(tmp_path, str(i), "ollama", "other", i + 2, decode=30)
    records, available = latest(host)
    assert available
    assert records[("dwarfstar", "ds")]["metric"] == "end_to_end_tps"
    assert records[("dwarfstar", "ds")]["value"] == 15.4


def test_stopped_engine_matches_explicit_router_model(tmp_path):
    jobs = tmp_path / "jobs"; jobs.mkdir()
    save(jobs, "run", "dwarfstar", "ds", 1, end=15.4)
    (tmp_path / "inference-router.json").write_text(json.dumps({"routes": {"public": {"backend": "dwarfstar", "model": "ds"}}}))
    host = SimpleNamespace(path=tmp_path / "management.json", settings={"jobs_directory": str(jobs)})
    result = decorate(host, {"dwarfstar": {"status": "stopped", "loaded": False}})
    assert result["dwarfstar"]["configured_model"] == "ds"
    assert result["dwarfstar"]["last_measurement"]["value"] == 15.4


def test_missing_history_is_not_falsely_unmeasured():
    host = SimpleNamespace(settings={}, path=None)
    result = decorate(host, {"dwarfstar": {"status": "stopped"}})
    assert result["dwarfstar"]["measurements_available"] is False
