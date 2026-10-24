"""Durable benchmark comparison, legacy jobs and strict JSONL imports."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.operations import benchmark, jobs, scoreboard
from services.operations.litellm_config import OperationsError


@pytest.fixture
def management(tmp_path):
    return SimpleNamespace(settings={"jobs_directory": str(tmp_path)})


def payload(**values):
    return {"action": "measurement_import", "backend": "dwarfstar", "model": "deepseek-v4-flash",
            "jsonl": json.dumps({"elapsed_s": 91.0307001050005, "ttft_s": 0.5143331930012209,
                                 "output_tokens": 1439, "prompt_tokens": 15, "decode_tps": None}), **values}


def test_import_persists_deduplicates_and_keeps_unknowns(management):
    first = jobs.execute(management, payload(context_tokens=262144, model_size_bytes=86715396219))
    assert jobs.execute(management, payload(context_tokens=262144, model_size_bytes=86715396219))["status"] == "already imported"
    # A fresh management object sees the durable record.
    reloaded = SimpleNamespace(settings=management.settings.copy())
    result = jobs.execute(reloaded, {"action": "measurements"})
    row, = result["measurements"]
    assert row["id"] == first["id"]
    assert row["summary"]["end_to_end_tps"] == pytest.approx(15.807853815692589)
    assert row["summary"]["decode_tps"] is None
    assert row["warmup"] is None and row["temperature"] is None
    assert row["environment"]["context_tokens"] == 262144
    detail = jobs.execute(reloaded, {"action": "job_detail", "id": row["id"]})
    assert detail["job"]["result"]["samples"][0]["output_tokens"] == 1439


@pytest.mark.parametrize("row", [[], {"elapsed_s": 0, "output_tokens": 1},
    {"elapsed_s": 1, "output_tokens": True}, {"elapsed_s": 1, "output_tokens": float("nan")},
    {"elapsed_s": 1, "output_tokens": 2, "decode_tps": float("inf")},
    {"elapsed_s": 1, "output_tokens": 2, "ttft_s": 2}])
def test_reject_invalid_sample_without_writing(management, row):
    with pytest.raises(OperationsError):
        jobs.execute(management, payload(jsonl=json.dumps(row)))
    assert not list(jobs.root(management).glob("*/job.json"))


def test_legacy_benchmarks_outside_recent_job_limit_and_filters(management):
    root = jobs.root(management)
    for index in range(105):
        folder = root / f"{index:032x}"
        folder.mkdir()
        jobs.write_json(folder / "job.json", {"id": folder.name, "state": "completed", "created_at": index,
            "specification": {"kind": "benchmark", "backend": "strata", "model": f"model-{index}", "prompt": "secret prompt"},
            "result": {"summary": {"end_to_end_tps": index}, "samples": [{"prompt_tokens": 15, "output_tokens": 1439}]}})
    result = scoreboard.execute(management, {"action": "measurements", "page": 1})
    assert result["total"] == 105 and len(result["measurements"]) == 5
    assert result["measurements"][-1]["model"] == "model-0"
    assert "secret prompt" not in json.dumps(result)
    assert result["measurements"][0]["summary"]["output_tokens"] == 1439
    filtered = scoreboard.execute(management, {"action": "measurements", "model": "model-0"})
    assert filtered["total"] == 1
    assert scoreboard.execute(management, {"action": "measurements", "backend": "dwarfstar"})["total"] == 0


def test_global_numeric_sort_places_missing_last(management):
    scoreboard.execute(management, payload())
    first = scoreboard.execute(management, payload(jsonl=json.dumps({"elapsed_s": 2, "output_tokens": 100, "decode_tps": 60})))
    result = scoreboard.execute(management, {"action": "measurements", "sort": "decode_tps"})
    assert result["measurements"][0]["id"] == first["id"]


def test_new_run_captures_environment_outside_sample_and_excludes_warmup(monkeypatch):
    samples = iter([{"end_to_end_tps": 1}, {"end_to_end_tps": 10}, {"end_to_end_tps": 20}])
    events = []
    monkeypatch.setattr(benchmark, "sample", lambda *_: (events.append("sample"), next(samples))[1])
    monkeypatch.setattr(benchmark, "snapshot", lambda *_: (events.append("snapshot"), {"context_tokens": 262144})[1])
    result = benchmark.run(None, {"backend": "dwarfstar", "model": "test", "prompt": "fixed", "warmup": True, "runs": 2})
    assert result["summary"]["end_to_end_tps"] == 15
    assert result["environment"]["context_tokens"] == 262144
    assert len(result["prompt_sha256"]) == 64
    assert events == ["sample", "sample", "snapshot", "sample"]



def test_import_and_list_through_real_host_transport(tmp_path):
    import asyncio
    from services.operations.litellm import HostOperations
    config = tmp_path / "management.json"
    config.write_text(json.dumps({"jobs_directory": str(tmp_path / "jobs"),
                                  "audit_directory": str(tmp_path / "audit")}), encoding="utf-8")
    host = HostOperations({"ODYSSEUS_OPERATIONS_HOST_HELPER": str(Path(__file__).resolve().parents[1] / "scripts/odysseus-litellm.py"),
                           "ODYSSEUS_OPERATIONS_MANAGEMENT_CONFIG": str(config)})
    first = asyncio.run(host.run("measurement_import", payload()))
    rows = asyncio.run(host.run("measurements", {"sort": "end_to_end_tps"}))
    assert rows["measurements"][0]["id"] == first["id"]
    detail = asyncio.run(host.run("job_detail", {"id": first["id"]}))
    assert detail["job"]["state"] == "completed"
    from services.operations.audit import read
    assert any(row["event"] == "operation.completed" for row in read(tmp_path / "audit")["events"])



def test_snapshot_uses_live_context_and_labels_declared_size(monkeypatch):
    from services.operations import dwarfstar, overview
    calls = []
    monkeypatch.setattr(dwarfstar, "inventory", lambda url: (calls.append(url),
                        {"max_context": 262144, "model": "DeepSeek V4 Flash 0731"})[1])
    monkeypatch.setattr(overview, "memory", lambda: {"available_bytes": 10})
    result = benchmark.snapshot(SimpleNamespace(dwarfstar_url="http://native"),
                                {"backend": "dwarfstar", "declared": {"model_size_bytes": 100}})
    assert result["context_tokens"] == 262144
    assert result["model_size_bytes"] == 100
    assert result["model_size_source"] == "user supplied artifact size"
    assert result["memory_after_sample"]["available_bytes"] == 10
    assert calls == ["http://native"]
