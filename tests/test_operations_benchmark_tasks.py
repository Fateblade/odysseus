"""Task reproducibility, cache separation, raised limits and historical compatibility."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from services.operations import benchmark, benchmark_tasks, jobs, scoreboard
from services.operations.litellm_config import OperationsError


@pytest.mark.parametrize("family", list(benchmark_tasks.FAMILIES))
@pytest.mark.parametrize("size", list(benchmark_tasks.SIZES))
def test_presets_are_reproducible_and_have_real_different_sources(family, size):
    first = benchmark_tasks.task(family, size, 0)
    assert first == benchmark_tasks.task(family, size, 0)
    second = benchmark_tasks.task(family, size, 1)
    assert first["prompt_sha256"] != second["prompt_sha256"]
    assert first["prompt"].split("\n")[1] != second["prompt"].split("\n")[1]
    assert first["characters"] >= benchmark_tasks.SIZES[size]
    assert hashlib.sha256(first["prompt"].encode()).hexdigest() == first["prompt_sha256"]


def test_different_tasks_and_warmup_are_all_distinct():
    plan = benchmark_tasks.plan({"task_family": "mixed", "task_size": "large", "task_mode": "different"}, 20)
    all_tasks = [*plan["tasks"], plan["warmup_task"]]
    assert len({item["prompt_sha256"] for item in all_tasks}) == 21
    assert {item["family"] for item in plan["tasks"]} == set(benchmark_tasks.FAMILIES)
    assert len(json.dumps(plan).encode()) < 2 * 1024 ** 2


def test_repeated_preset_reuses_exact_task():
    plan = benchmark_tasks.plan({"task_family": "research"}, 3)
    assert plan["tasks"][0] == plan["tasks"][1] == plan["warmup_task"]


@pytest.mark.parametrize("changes", [{"task_family": []}, {"task_size": "huge"}, {"task_mode": "disable-cache"},
                                      {"task_family": "custom", "task_mode": "different", "prompt": "test"}])
def test_invalid_task_settings_fail_with_validation_error(changes):
    with pytest.raises(OperationsError):
        benchmark_tasks.plan(changes, 3)


def test_job_accepts_256k_output_cap_and_long_timeout_but_rejects_over_limit(tmp_path, monkeypatch):
    management = SimpleNamespace(settings={"jobs_directory": str(tmp_path)}, dwarfstar_url="http://native")
    monkeypatch.setattr(jobs, "enqueue", lambda management, spec: spec)
    payload = {"action": "job_start", "kind": "benchmark", "backend": "dwarfstar", "model": "test",
               "runs": 2, "task_family": "research", "task_mode": "different", "max_tokens": 262144, "timeout": 604800}
    result = jobs.execute(management, payload)
    assert result["max_tokens"] == 262144 and result["timeout"] == 604800
    assert len(result["tasks"]) == 2
    with pytest.raises(OperationsError):
        jobs.execute(management, {**payload, "max_tokens": 262145})


def test_measured_tasks_exclude_warmup_and_save_per_family_summaries(monkeypatch):
    plan = benchmark_tasks.plan({"task_family": "mixed", "task_mode": "different"}, 5)
    prompts = []
    def sample(management, spec):
        prompts.append(spec["prompt"])
        return {"end_to_end_tps": len(prompts), "prompt_tokens": 100, "output_tokens": 50}
    monkeypatch.setattr(benchmark, "sample", sample)
    monkeypatch.setattr(benchmark, "snapshot", lambda *_: {})
    result = benchmark.run(None, {**plan, "backend": "dwarfstar", "model": "test", "runs": 5, "warmup": True})
    assert len(set(prompts)) == 6
    assert result["summary"]["end_to_end_tps"] == 4
    assert result["prompt_sha256"] is None
    assert set(result["task_summaries"]) == set(benchmark_tasks.FAMILIES)
    assert all(row["runs"] == 1 for row in result["task_summaries"].values())
    assert "prompt" not in result["samples"][0]["task"]


def test_historical_jobs_survive_with_original_measurements_and_new_jobs_filter(tmp_path):
    management = SimpleNamespace(settings={"jobs_directory": str(tmp_path)})
    for index, result in enumerate((
        {"summary": {"end_to_end_tps": 15.81}, "samples": [{"ttft_s": 0.514}]},
        {"benchmark_version": 2, "workload": {"task_family": "research", "task_size": "small", "task_mode": "different"},
         "summary": {"end_to_end_tps": 14.0}, "samples": []})):
        folder = tmp_path / f"{index:032x}"; folder.mkdir()
        jobs.write_json(folder / "job.json", {"id": folder.name, "state": "completed", "created_at": index,
                        "specification": {"kind": "benchmark", "backend": "dwarfstar", "model": "test", "prompt": "original"}, "result": result})
    before = [(path, path.read_bytes()) for path in tmp_path.glob("*/job.json")]
    historical = scoreboard.execute(management, {"action": "measurements", "workload": "historical"})
    assert historical["total"] == 1 and historical["measurements"][0]["summary"]["end_to_end_tps"] == 15.81
    assert historical["measurements"][0]["summary"]["ttft_s"] == 0.514
    assert scoreboard.execute(management, {"action": "measurements", "workload": "research"})["total"] == 1
    assert all(path.read_bytes() == contents for path, contents in before)


def test_jobs_list_excludes_prompt_plan_but_exposes_catalog(tmp_path):
    management = SimpleNamespace(settings={"jobs_directory": str(tmp_path)})
    folder = tmp_path / ("a" * 32); folder.mkdir()
    jobs.write_json(folder / "job.json", {"id": folder.name, "state": "completed", "created_at": 1,
        "specification": {"kind": "benchmark", "prompt": "private", "tasks": [{"prompt": "private"}], "warmup_task": {"prompt": "private"}}})
    result = jobs.execute(management, {"action": "jobs"})
    assert "private" not in json.dumps(result)
    assert result["benchmark"]["limits"]["output_tokens"] == 262144



def test_native_cached_prompt_tokens_are_recorded_when_reported(monkeypatch):
    import io
    from services.operations import benchmark
    stream = b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\ndata: {"usage":{"prompt_tokens":12,"completion_tokens":7,"prompt_tokens_details":{"cached_tokens":3}}}\n\ndata: [DONE]\n'
    monkeypatch.setattr(benchmark, "build_opener", lambda *_: SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(stream)))
    management = SimpleNamespace(settings={"audit_capture_content": False}, ollama_url="", strata_url="", dwarfstar_url="http://native", maya_url="")
    result = benchmark._sample(management, {"backend": "dwarfstar", "model": "test", "prompt": "Example", "max_tokens": 10})
    assert result["prompt_cached_tokens"] == 3
    assert result["output_tokens"] == 7 and result["decode_tps"] is None



def test_task_set_seed_replays_sources_and_fresh_default_changes_them(monkeypatch):
    seeds = iter((10, 11))
    monkeypatch.setattr(benchmark_tasks.secrets, "randbelow", lambda maximum: next(seeds))
    payload = {"task_family": "research", "task_mode": "different"}
    first = benchmark_tasks.plan(payload, 3)
    fresh = benchmark_tasks.plan(payload, 3)
    replay = benchmark_tasks.plan({**payload, "task_seed": first["task_seed"]}, 3)
    assert replay == first
    assert fresh["tasks"][0]["prompt_sha256"] != first["tasks"][0]["prompt_sha256"]
    with pytest.raises(OperationsError):
        benchmark_tasks.plan({**payload, "task_seed": 2147483648}, 3)



@pytest.mark.parametrize("body,detail", [
    ({"error": {"message": "prompt (1000 tokens) + max tokens (262144) exceeds the context (4096)"}}, "exceeds the context"),
    ({"error": "Unknown model"}, "Unknown model"),
    ({"detail": "Unsupported sampling field"}, "Unsupported sampling field")])
def test_http_rejection_preserves_structured_reason_without_endpoint_or_headers(monkeypatch, body, detail):
    import io
    from urllib.error import HTTPError
    def rejected(*args, **kwargs):
        raise HTTPError("http://secret-user:secret-password@native/v1/chat/completions", 400, "Bad Request",
                        {"Authorization": "Bearer private-token"}, io.BytesIO(json.dumps(body).encode()))
    monkeypatch.setattr(benchmark, "build_opener", lambda *_: SimpleNamespace(open=rejected))
    management = SimpleNamespace(settings={}, ollama_url="", strata_url="", dwarfstar_url="", maya_url="http://native")
    with pytest.raises(benchmark.BackendRequestError) as caught:
        benchmark._sample(management, {"backend": "maya", "model": "test", "prompt": "Example", "max_tokens": 262144,
                                       "benchmark_task_id": "v2/research/small/123/3"})
    text = str(caught.value)
    assert detail in text and "HTTP 400" in text and "262144" in text and "v2/research" in text
    assert "secret-password" not in text and "private-token" not in text
    assert caught.value.status_code == 400


@pytest.mark.parametrize("raw", [b'<html>private opaque error page</html>', b'x' * 17000])
def test_http_rejection_does_not_dump_non_json_or_unbounded_body(raw):
    import io
    from urllib.error import HTTPError
    error = HTTPError("http://native", 400, "Bad Request", {}, io.BytesIO(raw))
    result = benchmark.http_rejection(error, "maya", {"prompt": "Example", "max_tokens": 256})
    assert result.detail is None
    assert "No structured error message" in str(result)
    assert "private opaque" not in str(result)


def test_failed_request_journal_preserves_status_and_backend_reason(tmp_path, monkeypatch):
    from services.operations.audit import read
    specification = {"backend": "maya", "model": "test", "prompt": "Example", "max_tokens": 262144}
    def rejected(*args):
        raise benchmark.BackendRequestError("maya", 400, "Output exceeds context", specification)
    monkeypatch.setattr(benchmark, "_sample", rejected)
    management = SimpleNamespace(settings={"audit_directory": str(tmp_path)})
    with pytest.raises(benchmark.BackendRequestError):
        benchmark.sample(management, specification)
    entries = read(tmp_path)["events"]
    failed = next(row for row in entries if row["event"] == "request.failed")
    assert failed["status_code"] == 400
    from services.operations.audit import read as inspect
    event = inspect(tmp_path, event_id=failed["id"])["event"]
    assert event["content"]["backend_error"] == "Output exceeds context"
