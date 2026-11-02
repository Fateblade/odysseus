"""Revisioned metadata sidecar integrated with the existing YAML review/apply transaction."""

import copy
import json
import time
from pathlib import Path

from services.operations import litellm_host as host
from services.operations.litellm_config import OperationsError, candidate, load_config, review, revision, snapshot
from services.operations.model_metadata import FIELDS, deployment_id, digest, fingerprint, resolve


def location(path):
    return Path(path).with_name(Path(path).name + ".metadata.json")


def invalidate(path):
    if not path or not location(path).exists():
        return
    from services.operations.jobs import write_json
    with host.configuration_lock(Path(path)):
        state = read(location(path))
        for source in state["sources"].values():
            source["status"] = "stale"
            source["last_detected"] = source.get("detected", {})
            source["detected"] = {}
        write_json(location(path), state)


def read(path):
    if not path.exists():
        return {"schema_version": 1, "sources": {}, "deployments": {}, "providers": []}
    try:
        raw = path.read_bytes()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError()
        state = json.loads(raw)
        if state.get("schema_version") != 1 or not all(isinstance(state.get(key), dict) for key in ("sources", "deployments")):
            raise ValueError()
        return state
    except (ValueError, OSError, AttributeError):
        raise OperationsError("Model metadata sidecar is invalid; restore it before editing.", 503) from None


def import_existing(raw, state):
    state = copy.deepcopy(state)
    for index, row in enumerate(load_config(raw)["model_list"]):
        key = deployment_id(row, index)
        if key in state["deployments"]:
            continue
        info = row.get("model_info") or {}
        overrides = {field: {"operation": "set", "value": info[field]} for field in FIELDS
                     if field in info and info[field] is not None}
        context = (info.get("odysseus") or {}).get("context_window")
        if context:
            overrides["context_window"] = {"operation": "set", "value": context}
        state["deployments"][key] = {"fingerprint": fingerprint(row), "overrides": overrides,
                                     "source_id": None, "imported": True}
    return state


def decorate(raw, state):
    result = snapshot(raw)
    result["metadata_revision"] = digest(state)
    safe_sources = copy.deepcopy(state["sources"])
    for source in safe_sources.values():
        if source.get("status") == "fresh" and time.time() - source.get("last_success_at", 0) > 900:
            source["status"] = "stale"
    result["metadata_sources"] = list(safe_sources.values())
    result["metadata_providers"] = state.get("providers", [])
    for index, row in enumerate(load_config(raw)["model_list"]):
        key = deployment_id(row, index)
        record = state["deployments"].get(key, {})
        source = safe_sources.get(record.get("source_id"), {})
        detected = source.get("detected", {})
        metadata = {"id": key, "source_id": record.get("source_id"), "overrides": record.get("overrides", {}),
                    "detected": detected, "source": source, "published": record.get("published"),
                    "identity_changed": record.get("fingerprint") != fingerprint(row)}
        metadata.update(resolve(detected, metadata["overrides"], strict=False))
        result["draft"]["models"][index]["metadata"] = metadata
    result["metadata_public"] = state.get("public", {})
    result["metadata_public_current"] = state.get("published_yaml_revision") == revision(raw)
    return result


def execute(path, container, base_url, payload, management):
    from services.operations.jobs import write_json
    from services.operations.model_metadata_sources import collect
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise OperationsError("The configured LiteLLM YAML path must be an existing absolute file.", 503)
    store_path = location(path)
    with host.configuration_lock(path):
        raw = host.read_config(path)
        state = import_existing(raw, read(store_path))
        action = payload.get("action")
        if action == "snapshot":
            return decorate(raw, state)
        if payload.get("revision") != revision(raw) or payload.get("metadata_revision") != digest(state):
            raise OperationsError("Configuration or metadata changed. Reload before reviewing.", 409)
        if action == "metadata_refresh":
            sources, providers = collect(management)
            now = time.time()
            for key, old in state["sources"].items():
                if key not in sources:
                    # Keep historical evidence visible, but never use unavailable observations as current constraints.
                    sources[key] = {**old, "status": "unavailable", "last_attempt_at": now,
                                    "last_detected": old.get("last_detected", old.get("detected", {})), "detected": {}}
            state.update(sources=sources, providers=providers)
            write_json(store_path, state)
            return decorate(raw, state)
        if action == "probe":
            return host.probe(raw, payload.get("draft"), payload.get("index"), container)
        draft = payload.get("draft")
        edited, config = candidate(raw, draft, metadata_state=state)
        from services.operations.model_metadata import prepare
        next_state = prepare(config, draft, state)
        next_state["published_yaml_revision"] = revision(edited)
        for key, record in next_state["deployments"].items():
            if record != state["deployments"].get(key):
                record["actor"] = payload.get("_audit", {}).get("actor", "admin")
        checked = review(raw, draft, metadata_state=state)
        transaction = digest([revision(edited), next_state])
        if action == "preview":
            return {**checked, "review_digest": transaction, "metadata_public": next_state.get("public", {}),
                    "changed": edited != raw or next_state != state}
        if action != "apply" or payload.get("review_digest") != transaction:
            raise OperationsError("Review these exact metadata changes before applying.", 409)
        result = host.apply(path, raw, {**payload, "review_digest": revision(edited)}, container, base_url,
                            metadata_state=state, persist=lambda: write_json(store_path, next_state))
        return {**result, **decorate(path.read_bytes(), next_state)}
