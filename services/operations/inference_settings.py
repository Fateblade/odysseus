"""Reviewed host-owned router configuration; the UI edits retention only."""

import hashlib
import json
import math
from pathlib import Path

from services.operations.jobs import write_json
from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import configuration_lock


def location(management):
    value = management.settings.get("inference_router_config")
    return Path(value).expanduser() if value else management.path.parent / "inference-router.json" if management.path else None


def read(path):
    try:
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("routes"), dict):
            raise ValueError()
        for alias, route in value["routes"].items():
            ttl = route.get("keep_alive_seconds", 600)
            if not isinstance(alias, str) or not isinstance(route, dict) or type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl < 0:
                raise ValueError()
        return raw, value
    except (OSError, ValueError, TypeError, AttributeError):
        raise OperationsError("The inference router configuration cannot be read. Check its JSON and file permissions.", 503) from None


def snapshot(management):
    path = location(management)
    if not path or not path.exists():
        return {"configured": True, "installed": False, "routes": [], "message": "Install the on-demand inference router to configure model keep-alive here."}
    raw, value = read(path)
    return {"configured": True, "installed": True, "revision": hashlib.sha256(raw).hexdigest(),
            "routes": [{"name": alias, "backend": route.get("backend"), "model": route.get("model"),
                        "keep_alive_seconds": route.get("keep_alive_seconds", 600)} for alias, route in value["routes"].items()]}


def update(management, payload):
    path = location(management)
    if not path or not path.exists():
        raise OperationsError("Install the inference router first.", 503)
    with configuration_lock(path):
        raw, value = read(path)
        if payload.get("revision") != hashlib.sha256(raw).hexdigest():
            raise OperationsError("Router settings changed. Reload before saving.", 409)
        changes = payload.get("keep_alive_seconds")
        if not isinstance(changes, dict) or set(changes) != set(value["routes"]):
            raise OperationsError("Supply a keep-alive duration for each configured route.")
        for alias, ttl in changes.items():
            if type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl < 0:
                raise OperationsError("Keep-alive must be a finite number of seconds, zero or greater.")
            value["routes"][alias]["keep_alive_seconds"] = ttl
        backup = path.with_name(path.name + ".before-keep-alive")
        write_json(backup, json.loads(raw))
        write_json(path, value)
    return snapshot(management)
