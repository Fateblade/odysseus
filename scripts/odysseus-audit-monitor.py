#!/usr/bin/env python3
"""Observe residency without loading models; persist transitions while the UI is closed."""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.operations.audit import directory, emit
from services.operations.dwarfstar import inventory
from services.operations.jobs import write_json
from services.operations.litellm_host import get_json
from services.operations.management import Management


def sample(management):
    def ollama():
        data = get_json(management.ollama_url.rstrip("/") + "/api/ps")
        return sorted([{"model": row.get("name") or row.get("model"), "context": row.get("context_length")}
                       for row in data["models"]], key=lambda row: str(row["model"]))

    def strata():
        data = get_json(management.strata_url.rstrip("/") + "/health")
        if type(data.get("loaded")) is not bool:
            raise ValueError()
        return [{"model": data.get("model"), "context": data.get("max_context")}] if data["loaded"] else []

    def dwarfstar():
        data = inventory(management.dwarfstar_url)
        if data.get("status") != "ok":
            raise ValueError()
        return [{"model": data.get("model"), "context": data.get("max_context")}]

    probes = {"ollama": ollama, "strata": strata}
    if management.dwarfstar_url:
        probes["dwarfstar"] = dwarfstar
    if management.maya_url:
        from services.operations.maya import inventory as maya_inventory

        def maya():
            data = maya_inventory(management.maya_url)
            if data.get("status") == "unloaded":
                return []
            if data.get("status") != "ok":
                raise ValueError()
            return [{"model": data.get("model"), "context": data.get("max_context")}]

        probes["maya"] = maya
    result = {}
    with ThreadPoolExecutor(max_workers=len(probes)) as executor:
        futures = {name: executor.submit(probe) for name, probe in probes.items()}
        for name, future in futures.items():
            try:
                result[name] = future.result()
            except Exception:
                result[name] = None
    return result


def record_transitions(folder, previous, current):
    for backend, models in current.items():
        old = previous.get(backend, {})
        if models is None:
            if old.get("available") is not False:
                emit(folder, "backend.unavailable", source="residency-observer", backend=backend)
            previous[backend] = {**old, "available": False}
            continue
        if "models" not in old:
            emit(folder, "residency.baseline", source="residency-observer", backend=backend, current=models)
        elif models != old["models"]:
            emit(folder, "residency.changed", source="residency-observer", backend=backend,
                 previous=old["models"], current=models)
        if old.get("available") is False:
            emit(folder, "backend.available", source="residency-observer", backend=backend)
        previous[backend] = {"models": models, "available": True}
    return previous


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--management-config", required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--strata-url", default="http://127.0.0.1:8081")
    parser.add_argument("--dwarfstar-url", default="")
    parser.add_argument("--maya-url", default="")
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not 2 <= args.interval <= 300:
        parser.error("interval must be between 2 and 300 seconds")
    management = Management(args.management_config, args.strata_url, args.ollama_url, args.dwarfstar_url, args.maya_url)
    folder = directory(management)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "residency-state.json"
    try:
        previous = json.loads(path.read_text())
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    emit(folder, "observer.started", source="residency-observer")
    while True:
        previous = record_transitions(folder, previous, sample(management))
        write_json(path, previous)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
