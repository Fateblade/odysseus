"""Streaming TPS measurements with backend token counts, never chunk estimates."""

import hashlib
import json
import platform
import time
from urllib.request import Request, build_opener

from services.operations.litellm_host import NoRedirect


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
        emit(directory(management), "request.failed", **fields, error_type=type(error).__name__)
        raise
    emit(directory(management), "request.succeeded", **fields, prompt_tokens=result["prompt_tokens"],
         output_tokens=result["output_tokens"], duration_ms=result["elapsed_s"] * 1000,
         content=result.pop("captured_response", None))
    return result


def _sample(management, specification):
    backend = specification["backend"]
    ollama = backend == "ollama"
    endpoints = {"ollama": management.ollama_url, "strata": management.strata_url, "dwarfstar": management.dwarfstar_url, "maya": management.maya_url}
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
    if backend == "maya":
        payload["reasoning_effort"] = "none"
    request = Request(base.rstrip("/") + ("/api/generate" if ollama else "/v1/chat/completions"),
                      data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    first, usage, final, timings = None, {}, {}, {}
    captured, captured_size = [], 0
    completed = False
    with build_opener(NoRedirect()).open(request, timeout=300) as response:
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
                raise RuntimeError("Benchmark backend returned an error.")
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
    offered, accepted = timings.get("draft_n"), timings.get("draft_n_accepted")
    result["speculative_acceptance"] = (accepted / offered if type(offered) is int and offered > 0
                                         and type(accepted) is int and 0 <= accepted <= offered else None)
    result["end_to_end_tps"] = tokens / elapsed if tokens else None
    if management.settings.get("audit_capture_content", True):
        result["captured_response"] = {"chunks": captured, "truncated": captured_size >= 2 * 1024 * 1024}
    return result


def run(management, specification):
    warmup = None
    if specification["warmup"]:
        print("Warmup (excluded from measured samples)", flush=True)
        warmup = sample(management, specification)
    environment = None
    samples = []
    for index in range(specification["runs"]):
        result = sample(management, specification)
        samples.append(result)
        if environment is None:
            environment = snapshot(management, specification)
        print(json.dumps({"run": index + 1, **result}), flush=True)
    from services.operations.scoreboard import summarize
    summary = summarize(samples)
    return {"backend": specification["backend"], "model": specification["model"], "summary": summary,
            "samples": samples, "warmup": warmup, "temperature": 0, "seed": 42,
            "environment": environment, "prompt_sha256": hashlib.sha256(specification["prompt"].encode()).hexdigest(),
            "thinking": False if specification["backend"] in ("dwarfstar", "maya") else "backend default",
            "note": "Warmup does not clear caches. End-to-end TPS includes load, queue and prefill; unavailable timings stay null."}


def snapshot(management, specification):
    """Read settings outside the timed request; missing metadata never fails a run."""
    from services.operations import overview
    from services.operations.dwarfstar import inventory
    from services.operations.maya import inventory as maya_inventory
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
                    maya_inventory(management.maya_url) if backend == "maya" else overview.strata(management.strata_url))
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
