"""Compact exact-model benchmark readouts; never expose stored request content."""

import json
import math

from services.operations.litellm_config import OperationsError


def latest(management):
    from services.operations.jobs import root, read_job
    records, available = {}, True
    try:
        folders = list(root(management).iterdir())
    except (OperationsError, OSError):
        return {}, False
    for folder in folders:
        if not folder.is_dir() or not (folder / "job.json").is_file():
            continue
        try:
            job = read_job(folder)
            spec, result = job.get("specification", {}), job.get("result", {})
            if job.get("state") != "completed" or spec.get("kind") != "benchmark" or not isinstance(result, dict):
                continue
            summary = result.get("summary", {})
            if not summary:
                from services.operations.scoreboard import summarize
                summary = summarize(result.get("samples", []))
            metric = next((key for key in ("decode_tps", "end_to_end_tps") if type(summary.get(key)) in (int, float) and math.isfinite(summary[key]) and summary[key] >= 0), None)
            if metric is None:
                continue
            record = {"id": job["id"], "time": job["created_at"], "metric": metric, "value": summary[metric],
                      "context_tokens": result.get("environment", {}).get("context_tokens")}
            names = {spec.get("model"), result.get("environment", {}).get("reported_model")}
            for name in names:
                if isinstance(name, str) and name:
                    key = (spec.get("backend"), name)
                    if key not in records or record["time"] > records[key]["time"]:
                        records[key] = record
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            available = False
    return records, available


def decorate(management, data):
    if management is None:
        return data
    records, available = latest(management)
    routed = {}
    try:
        from services.operations.inference_settings import location, read
        path = location(management)
        if path and path.is_file():
            _, config = read(path)
            for route in config["routes"].values():
                if isinstance(route, dict) and isinstance(route.get("model"), str):
                    routed.setdefault(route.get("backend"), set()).add(route["model"])
    except (OperationsError, OSError):
        pass
    for backend in ("strata", "maya", "maya_glm", "dwarfstar"):
        value = data.get(backend)
        if not isinstance(value, dict):
            continue
        try:
            if backend == "strata":
                from services.operations.strata_config import profile
                settings, _, path, _ = profile(management)
                if path.stat().st_size > 1048576:
                    raise ValueError("Strata configuration exceeds size limit")
                config = json.loads(path.read_text(encoding="utf-8-sig"))
                value.update(configured_model=config.get("model_name"), family=settings.get("family"), quantization=settings.get("model"))
            elif backend in ("maya", "maya_glm"):
                from services.operations.maya_settings import config_path
                path = config_path(management, backend)
                if path.is_file() and path.stat().st_size <= 1048576:
                    value["configured_model"] = json.loads(path.read_text(encoding="utf-8-sig")).get("model_name")
        except (OperationsError, OSError, ValueError, KeyError, TypeError):
            pass
        if not value.get("configured_model") and len(routed.get(backend, set())) == 1:
            value["configured_model"] = next(iter(routed[backend]))
        names = [value.get("model"), *(value.get("aliases") or [])]
        if not value.get("loaded"):
            names.append(value.get("configured_model"))
        matches = [records[(backend, name)] for name in names if isinstance(name, str) and (backend, name) in records]
        value["last_measurement"] = max(matches, key=lambda item: item["time"]) if matches else None
        value["measurements_available"] = available
    for model in [*data.get("ollama", {}).get("loaded", []), *data.get("ollama", {}).get("installed", [])]:
        model["last_measurement"] = records.get(("ollama", model.get("name")))
        model["measurements_available"] = available
    return data
