"""Selected model allocations and native Maya task budgets before queueing."""

from types import SimpleNamespace

import pytest

from services.operations import benchmark_limits, benchmark_tasks, dwarfstar, maya, overview, jobs
from services.operations.litellm_config import OperationsError


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(maya, "inventory", lambda _: {"status": "ok", "loaded": True, "aliases": ["model"],
                         "model": "model", "max_context": 128000})
    return SimpleNamespace(maya_url="http://maya", dwarfstar_url="http://ds4", ollama_url="http://ollama", strata_url="http://strata")


def test_selected_maya_reports_active_context_and_ceiling(host):
    result = benchmark_limits.limits(host, {"backend": "maya", "model": "model"})
    assert result["context_tokens"] == 128000
    assert result["max_output_tokens"] == 127991
    assert not result["exact"]
    with pytest.raises(OperationsError, match="128,000"):
        benchmark_limits.validate_budget(result, 262144)


def test_native_task_count_uses_largest_including_warmup(host, monkeypatch):
    workload = benchmark_tasks.plan({"task_family": "code_review", "task_mode": "different", "task_seed": 12}, 2)
    counts, sent = iter((10000, 11000, 12000)), []
    def tokens(url, path, body):
        sent.append((url, path, body))
        return {"tokens": next(counts), "max_context": 128000, "approximate": False}
    monkeypatch.setattr(benchmark_limits, "api", tokens)
    result = benchmark_limits.limits(host, {"backend": "maya", "model": "model", "warmup": True}, workload)
    assert result["prompt_tokens"] == 12000 and result["max_output_tokens"] == 115992
    assert result["exact"] and result["task_seed"] == 12
    assert all(path == "/api/tokens" and body["reasoning_effort"] == "none" for _, path, body in sent)
    assert len(sent) == 3
    benchmark_limits.validate_budget(result, 115992)
    with pytest.raises(OperationsError, match="largest prompt 12,000"):
        benchmark_limits.validate_budget(result, 115993)


def test_repeated_tasks_count_only_once(host, monkeypatch):
    calls = []
    monkeypatch.setattr(benchmark_limits, "api", lambda *args: (calls.append(args),
                        {"tokens": 200, "max_context": 128000, "approximate": False})[1])
    result = benchmark_limits.limits(host, {"backend": "maya", "model": "model", "validate_tasks": True,
                                           "prompt": "Example", "runs": 20})
    assert result["max_output_tokens"] == 127792 and len(calls) == 1


@pytest.mark.parametrize("tokens,context,approximate", [(128000, 128000, False), (1000, 64000, False), (1000, 128000, True)])
def test_overflow_changed_context_and_approximate_counts_are_not_accepted(host, monkeypatch, tokens, context, approximate):
    monkeypatch.setattr(benchmark_limits, "api", lambda *_: {"tokens": tokens, "max_context": context, "approximate": approximate})
    payload = {"backend": "maya", "model": "model", "validate_tasks": True, "prompt": "Example", "runs": 1}
    with pytest.raises(OperationsError):
        result = benchmark_limits.limits(host, payload)
        benchmark_limits.validate_budget(result, 1)


def test_ollama_uses_loaded_allocation_not_model_training_ceiling(host, monkeypatch):
    monkeypatch.setattr(overview, "ollama", lambda _: {"loaded": [{"name": "model:latest", "context_length": 4096}],
                                                    "installed": [{"name": "model:latest", "context_length": 131072}]})
    assert benchmark_limits.limits(host, {"backend": "ollama", "model": "model"})["context_tokens"] == 4096
    monkeypatch.setattr(overview, "ollama", lambda _: {"loaded": [], "installed": [{"context_length": 131072}]})
    assert benchmark_limits.limits(host, {"backend": "ollama", "model": "model"})["context_tokens"] is None


def test_dwarfstar_aliases_use_one_active_model_context(host, monkeypatch):
    monkeypatch.setattr(dwarfstar, "inventory", lambda _: {"status": "ok", "loaded": True, "aliases": ["model"], "max_context": 262144})
    result = benchmark_limits.limits(host, {"backend": "dwarfstar", "model": "model"})
    assert result["max_output_tokens"] == 262143 and not result["exact"]
    with pytest.raises(OperationsError, match="not the model"):
        benchmark_limits.limits(host, {"backend": "dwarfstar", "model": "wrong-model"})


def test_job_rechecks_context_before_queueing(host, tmp_path, monkeypatch):
    host.settings = {"jobs_directory": str(tmp_path)}
    monkeypatch.setattr(benchmark_limits, "api", lambda *_: {"tokens": 1000, "max_context": 128000, "approximate": False})
    queued = []
    monkeypatch.setattr(jobs, "enqueue", lambda *args: queued.append(args))
    with pytest.raises(OperationsError, match="largest prompt 1,000"):
        jobs.execute(host, {"action": "job_start", "kind": "benchmark", "backend": "maya", "model": "model",
                           "runs": 1, "prompt": "Example", "max_tokens": 262144})
    assert not queued
