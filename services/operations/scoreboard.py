"""Persistent measurement views over host jobs, including imported script samples."""

import hashlib
import json
import math
import statistics
import time

from services.operations.litellm_config import OperationsError

METRICS = ("ttft_s", "decode_tps", "prefill_tps", "end_to_end_tps", "elapsed_s",
           "prompt_tokens", "output_tokens", "speculative_acceptance")


def summarize(samples):
    return {key: statistics.median(values) if values else None for key in METRICS
            for values in [[row[key] for row in samples if row.get(key) is not None]]}


def card(job):
    spec, result = job["specification"], job.get("result", {})
    return {"id": job["id"], "created_at": job["created_at"], "backend": spec.get("backend"),
            "model": spec.get("model"), "source": result.get("source", "host benchmark"),
            "summary": {**summarize(result.get("samples", [])), **result.get("summary", {})}, "runs": len(result.get("samples", [])),
            "max_tokens": spec.get("max_tokens"), "warmup": spec.get("warmup"),
            "prompt_sha256": result.get("prompt_sha256") or (hashlib.sha256(spec["prompt"].encode()).hexdigest() if spec.get("prompt") else None), "environment": result.get("environment", {}),
            "temperature": result.get("temperature"), "seed": result.get("seed"),
            "thinking": result.get("thinking"), "notes": spec.get("notes", "")}


def execute(management, payload):
    from services.operations.jobs import root, read_job, write_json
    base = root(management)
    if payload["action"] == "measurements":
        page = payload.get("page", 0)
        if type(page) is not int or page < 0:
            raise OperationsError("Invalid measurement page.")
        records = []
        for folder in base.iterdir():
            if not folder.is_dir() or not (folder / "job.json").exists():
                continue
            job = read_job(folder)
            if job.get("state") == "completed" and job.get("specification", {}).get("kind") == "benchmark" and isinstance(job.get("result"), dict):
                records.append(card(job))
        backend, query = payload.get("backend", ""), payload.get("model", "")
        if not isinstance(backend, str) or not isinstance(query, str) or len(query) > 512:
            raise OperationsError("Invalid measurement filter.")
        records = [row for row in records if (not backend or row["backend"] == backend)
                   and query.casefold() in str(row["model"]).casefold()]
        sort = payload.get("sort", "created_at")
        if sort not in ("created_at", "end_to_end_tps", "decode_tps", "ttft_s"):
            raise OperationsError("Invalid measurement sort.")
        def order(row):
            value = row["created_at"] if sort == "created_at" else row["summary"].get(sort)
            return (value is None, value if sort == "ttft_s" and value is not None else -(value or 0), row["id"])
        records.sort(key=order)
        return {"measurements": records[page * 100:(page + 1) * 100], "total": len(records), "page": page}
    if payload["action"] != "measurement_import":
        raise OperationsError("Unknown measurement operation.")
    record = imported(payload)
    # Identical samples and declared settings produce the same ID on repeated imports.
    record_id = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:32]
    from services.operations.litellm_host import configuration_lock
    with configuration_lock(base / "measurements"):
        folder = base / record_id
        if (folder / "job.json").exists():
            return {"status": "already imported", "id": record_id}
        folder.mkdir(mode=0o700, exist_ok=True)
        write_json(folder / "job.json", {"id": record_id, "state": "completed", "created_at": time.time(),
                                         **record})
    return {"status": "imported", "id": record_id}


def imported(payload):
    from services.operations.management import model_name
    backend = payload.get("backend")
    if backend not in ("ollama", "strata", "dwarfstar", "maya"):
        raise OperationsError("Choose a supported backend.")
    text = payload.get("jsonl", "")
    if not isinstance(text, str) or not 1 <= len(text) <= 262144:
        raise OperationsError("Paste up to 256 KiB of script sample JSON lines.")
    try:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not 1 <= len(rows) <= 100:
            raise ValueError()
        samples = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError()
            sample = {}
            for key in METRICS:
                value = row.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                    raise ValueError()
                sample[key] = value
            if any(sample[key] is not None and (type(sample[key]) is not int) for key in ("prompt_tokens", "output_tokens")):
                raise ValueError()
            if sample["speculative_acceptance"] is not None and sample["speculative_acceptance"] > 1:
                raise ValueError()
            if not sample["elapsed_s"] or not sample["output_tokens"]:
                raise ValueError()
            if sample["ttft_s"] is not None and sample["ttft_s"] > sample["elapsed_s"]:
                raise ValueError()
            # Recompute client throughput from the original token count and wall clock.
            sample["end_to_end_tps"] = sample["output_tokens"] / sample["elapsed_s"]
            samples.append(sample)
    except (ValueError, TypeError, OverflowError):
        raise OperationsError("Use 1–100 sample JSON lines with positive elapsed_s and output_tokens and finite, nonnegative metrics.") from None
    environment = {"declared": declared_settings(payload)}
    for key in ("context_tokens", "model_size_bytes"):
        value = payload.get(key)
        if value is not None and (type(value) is not int or not 1 <= value <= 2 ** 53 - 1):
            raise OperationsError("Context and model size must be positive integers, or omitted when unknown.")
        environment[key] = value
    notes = payload.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise OperationsError("Notes must be at most 2000 characters.")
    prompt = payload.get("prompt")
    if prompt is not None and (not isinstance(prompt, str) or len(prompt) > 32768):
        raise OperationsError("Prompt must be at most 32768 characters.")
    return {"specification": {"kind": "benchmark", "backend": backend, "model": model_name(payload.get("model")),
                              "notes": notes, "runs": len(samples)},
            "result": {"samples": samples, "summary": summarize(samples), "environment": environment,
                       "source": "imported script (user supplied settings)",
                       "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest() if prompt else None}}



def declared_settings(payload):
    result = {}
    size = payload.get("model_size_bytes")
    if size is not None and (type(size) is not int or not 1 <= size <= 2 ** 53 - 1):
        raise OperationsError("Model artifact size must be a positive integer number of bytes.")
    result["model_size_bytes"] = size
    for key in ("quantization", "engine_version"):
        value = payload.get(key, "")
        if not isinstance(value, str) or len(value) > 200:
            raise OperationsError("Quantization and engine version must be at most 200 characters.")
        result[key] = value
    return result
