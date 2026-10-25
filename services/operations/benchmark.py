"""Streaming TPS measurements with backend token counts, never chunk estimates."""

import hashlib
import json
import platform
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from services.operations.litellm_host import NoRedirect


class BackendRequestError(RuntimeError):
    def __init__(self, backend, status_code, detail, specification):
        self.status_code = status_code
        self.detail = detail
        target = f"HTTP {status_code}" if status_code else "stream error"
        task = specification.get("benchmark_task_id", "custom")
        message = (f"{backend} rejected benchmark {task} ({target}; output cap {specification['max_tokens']} tokens; "
                   f"prompt {len(specification['prompt'])} characters).")
        message += " " + (detail or "No structured error message was returned. Inspect the backend service log.")
        if status_code == 400:
            message += " Check the requested model, sampling parameters and input plus output against the runtime context."
        super().__init__(message)


def error_detail(value):
    if not isinstance(value, dict):
        return None
    value = value.get("error") or value.get("detail") or value.get("message")
    if isinstance(value, dict):
        value = value.get("message")
    return " ".join(value.split())[:2048] if isinstance(value, str) else None


def http_rejection(error, backend, specification):
    detail = None
    try:
        raw = error.read(16385)
        if len(raw) <= 16384:
            detail = error_detail(json.loads(raw))
    except (OSError, ValueError, UnicodeError):
        pass
    finally:
        error.close()
    return BackendRequestError(backend, error.code, detail, specification)


def sample(management, specification):
    from services.operations.audit import directory, emit
    import uuid
    fields = {**getattr(management, "audit_actor", {}), "request_id": uuid.uuid4().hex,
              "job_id": getattr(management, "audit_job_id", None), "source": "benchmark",
              "backend": specification["backend"], "model": specification["model"]}
    emit(directory(management), "request.received", **fields,
         content={"prompt": specification["prompt"]} if management.settings.get("audit_capture_content", True) else None)
    try:
        result = _sample(management, specification)
    except Exception as error:
        emit(directory(management), "request.failed", **fields, error_type=type(error).__name__,
             status_code=getattr(error, "status_code", None),
             content={"backend_error": error.detail, "task_id": specification.get("benchmark_task_id")}
             if isinstance(error, BackendRequestError) and management.settings.get("audit_capture_content", True) else None)
        raise
    emit(directory(management), "request.succeeded", **fields, prompt_tokens=result["prompt_tokens"],
         output_tokens=result["output_tokens"], duration_ms=result["elapsed_s"] * 1000,
         content=result.pop("captured_response", None))
    return result


def _sample(management, specification):
    backend = specification["backend"]
    ollama = backend == "ollama"
    endpoints = {"ollama": management.ollama_url, "strata": management.strata_url, "dwarfstar": management.dwarfstar_url, "maya": management.maya_url, "maya_glm": getattr(management, "maya_glm_url", "")}
    base = endpoints.get(backend)
    if not base:
        raise RuntimeError("The benchmark backend is not configured.")
    payload = {"model": specification["model"], "stream": True}
    if ollama:
        payload.update(prompt=specification["prompt"], options={"num_predict": specification["max_tokens"], "temperature": 0, "seed": 42})
    else:
        payload.update(messages=[{"role": "user", "content": specification["prompt"]}],
                       max_tokens=specification["max_tokens"], temperature=0, seed=42, stream_options={"include_usage": True})
    if backend == "dwarfstar":
        payload["think"] = False
    if backend in ("maya", "maya_glm"):
        payload["reasoning_effort"] = "none"
    request = Request(base.rstrip("/") + ("/api/generate" if ollama else "/v1/chat/completions"),
                      data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    first, usage, final, timings = None, {}, {}, {}
    captured, captured_size = [], 0
    completed = False
    try:
        response = build_opener(NoRedirect()).open(request, timeout=300)
    except HTTPError as error:
        raise http_rejection(error, backend, specification) from None
    with response:
        while raw := response.readline(1024 * 1024 + 1):
            if len(raw) > 1024 * 1024:
                raise RuntimeError("Benchmark response line exceeded 1 MiB.")
            if not raw.strip():
                continue
            if not ollama:
                if not raw.startswith(b"data:"):
                    continue
                raw = raw[5:].strip()
                if raw == b"[DONE]":
                    completed = True
                    break
            event = json.loads(raw)
            if event.get("error"):
                raise BackendRequestError(backend, None, error_detail(event), specification)
            if ollama:
                emitted = event.get("response") or event.get("thinking")
                if event.get("done"):
                    final = event
                    completed = True
            else:
                emitted = any(c.get("delta", {}).get("content") or c.get("delta", {}).get("reasoning_content") for c in event.get("choices", []))
                usage = event.get("usage") or usage
                timings = event.get("timings") or timings
            if management.settings.get("audit_capture_content", True):
                fragment = {key: event.get(key) for key in ("response", "thinking", "choices") if event.get(key)}
                encoded = json.dumps(fragment)
                if fragment and captured_size < 2 * 1024 * 1024:
                    captured.append(fragment)
                    captured_size += len(encoded)
            if emitted:
                first = first or time.perf_counter()
    if not completed:
        raise RuntimeError("Benchmark stream ended without completion.")
    elapsed = time.perf_counter() - start
    tokens = final.get("eval_count") if ollama else usage.get("completion_tokens")
    result = {"elapsed_s": elapsed, "ttft_s": first - start if first else None,
              "output_tokens": tokens, "prompt_tokens": final.get("prompt_eval_count") if ollama else usage.get("prompt_tokens")}
    if ollama:
        duration = final.get("eval_duration", 0) / 1e9
        prompt_duration = final.get("prompt_eval_duration", 0) / 1e9
        prefill_tokens = max(0, final.get("prompt_eval_count", 0) - final.get("prompt_eval_cached_count", 0))
        result.update(decode_tps=tokens / duration if tokens and duration else None,
                      prefill_tps=prefill_tokens / prompt_duration if prefill_tokens and prompt_duration else None,
                      prompt_cached_tokens=final.get("prompt_eval_cached_count"),
                      load_s=final.get("load_duration", 0) / 1e9, measurement="backend durations")
    else:
        result.update(decode_tps=timings.get("predicted_per_second"), prefill_tps=timings.get("prompt_per_second"),
                      timings=timings, measurement="backend timings when supplied; client wall time otherwise")
    if not ollama:
        details = usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else None
        result["prompt_cached_tokens"] = cached if type(cached) is int and cached >= 0 else None
    offered, accepted = timings.get("draft_n"), timings.get("draft_n_accepted")
    result["speculative_acceptance"] = (accepted / offered if type(offered) is int and offered > 0
                                         and type(accepted) is int and 0 <= accepted <= offered else None)
    result["end_to_end_tps"] = tokens / elapsed if tokens else None
    if management.settings.get("audit_capture_content", True):
        result["captured_response"] = {"chunks": captured, "truncated": captured_size >= 2 * 1024 * 1024}
    return result


def run(management, specification):
    if specification["backend"] in ("ollama", "strata"):
        from services.operations.exclusivity import ensure_available
        ensure_available(management, specification["backend"])
    from services.operations.scoreboard import summarize
    legacy_task = {"id": "custom", "family": "custom", "size": None, "prompt": specification["prompt"],
                   "prompt_sha256": hashlib.sha256(specification["prompt"].encode()).hexdigest()}
    tasks = specification.get("tasks") or [legacy_task] * specification["runs"]
    def measure(item):
        result = sample(management, {**specification, "prompt": item["prompt"], "benchmark_task_id": item["id"]})
        result["task"] = {key: value for key, value in item.items() if key != "prompt"}
        return result
    warmup = None
    if specification["warmup"]:
        print("Warmup (excluded from measured samples)", flush=True)
        warmup = measure(specification.get("warmup_task") or tasks[0])
    environment = None
    samples = []
    for index, item in enumerate(tasks):
        result = measure(item)
        samples.append(result)
        if environment is None:
            environment = snapshot(management, specification)
        print(json.dumps({"run": index + 1, **result}), flush=True)
    fingerprints = [item["prompt_sha256"] for item in tasks]
    families = list(dict.fromkeys(item["family"] for item in tasks))
    return {"backend": specification["backend"], "model": specification["model"], "summary": summarize(samples),
            "samples": samples, "warmup": warmup, "temperature": 0, "seed": 42,
            "benchmark_version": specification.get("benchmark_version"),
            "context_budget": specification.get("context_budget"),
            "workload": {key: specification.get(key) for key in ("task_family", "task_size", "task_mode", "task_seed")},
            "workload_sha256": hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest(),
            "task_summaries": {family: {"runs": sum(row["task"]["family"] == family for row in samples),
                               "summary": summarize([row for row in samples if row["task"]["family"] == family])} for family in families},
            "environment": environment, "prompt_sha256": fingerprints[0] if len(set(fingerprints)) == 1 else None,
            "thinking": False if specification["backend"] in ("dwarfstar", "maya", "maya_glm") else "backend default",
            "note": "Warmup is excluded; caches are not cleared. Distinct tasks reduce shared prompt reuse but do not guarantee cache-free runs. Mixed-task medians combine different workloads. End-to-end TPS includes load, queue and prefill; unavailable timings stay null."}


def snapshot(management, specification):
    """Read settings outside the timed request; missing metadata never fails a run."""
    from services.operations import overview
    from services.operations.dwarfstar import inventory
    from services.operations.maya import inventory as maya_inventory, glm_inventory
    from pathlib import Path
    environment = {"sampled_at": time.time(), "system": platform.system(), "kernel": platform.release(),
                   "machine": platform.machine(), "context_tokens": None, "model_size_bytes": None}
    backend = specification["backend"]
    try:
        if backend == "ollama":
            data = overview.ollama(management.ollama_url)
            matches = [row for row in data.get("loaded", []) if row["name"] == specification["model"]]
            installed = [row for row in data.get("installed", []) if row["name"] == specification["model"]]
            if matches:
                environment.update(context_tokens=matches[0].get("context_length"), runtime_bytes=matches[0].get("size_bytes"))
            if installed:
                environment.update(model_size_bytes=installed[0].get("size_bytes"), model_digest=installed[0].get("digest"),
                                   model_size_source="Ollama installed artifact")
        else:
            data = (inventory(management.dwarfstar_url) if backend == "dwarfstar" else
                    maya_inventory(management.maya_url) if backend == "maya" else
                    glm_inventory(management.maya_glm_url) if backend == "maya_glm" else overview.strata(management.strata_url))
            environment.update(context_tokens=data.get("max_context"), reported_model=data.get("model"),
                               engine_version=data.get("version"))
        environment["memory_after_sample"] = overview.memory()
        cpu_info = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        environment["cpu"] = next((line.split(":", 1)[1].strip() for line in cpu_info.splitlines()
                                   if line.startswith("model name")), None)
    except Exception:
        environment["metadata_note"] = "Some runtime metadata could not be read."
    declared = specification.get("declared", {})
    environment["declared"] = declared
    if environment["model_size_bytes"] is None and declared.get("model_size_bytes"):
        environment.update(model_size_bytes=declared["model_size_bytes"], model_size_source="user supplied artifact size")
    return environment
