"""Reviewed Maya settings with legacy native context-reload support."""

import hashlib
import json
import time

from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import get_json
from services.operations.maya import inventory


def snapshot(management):
    from services.operations import maya_settings
    if maya_settings.enabled(management):
        return maya_settings.snapshot(management)
    if not management.maya_url:
        return {"configured": False, "message": "Configure the Maya host endpoint first."}
    live = inventory(management.maya_url)
    if live.get("status") != "ok":
        return {"configured": False, "message": "Maya must be loaded and ready before changing context."}
    try:
        limits = get_json(management.maya_url.rstrip("/") + "/api/context")
        low, high, current = (limits[key] for key in ("min", "max", "context"))
        if any(type(value) is not int for value in (low, high, current)) or not 1 <= low <= current <= high:
            raise ValueError()
        if current != live.get("max_context") or (limits.get("reload") or {}).get("state") in ("waiting", "running"):
            raise ValueError()
        maximum = management.settings.get("maya_setup", {}).get("maximum_context", 4096)
        if type(maximum) is not int or not low <= maximum <= high:
            raise ValueError()
        data = {"url": management.maya_url, "model": live["model"], "version": live["version"],
                "context": current, "minimum": low, "maximum": maximum}
        revision = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        return {"configured": True, "revision": revision, "model": live["model"],
                "active_context": current, "minimum_context": low, "maximum_context": maximum}
    except Exception:
        return {"configured": False, "message": "Maya context limits are unavailable, changing or invalid. Check maya_setup.maximum_context."}


def review(management, payload):
    from services.operations import maya_settings
    if maya_settings.enabled(management):
        return maya_settings.review(management, payload)
    current = snapshot(management)
    if not current["configured"]:
        raise OperationsError(current["message"], 503)
    if payload.get("revision") != current["revision"]:
        raise OperationsError("Maya state changed. Reload and review again.", 409)
    context = payload.get("context")
    if type(context) is not int or not current["minimum_context"] <= context <= current["maximum_context"]:
        raise OperationsError(f"Context must be between {current['minimum_context']} and {current['maximum_context']} tokens.")
    digest = hashlib.sha256(f"{current['revision']}:{context}".encode()).hexdigest()
    return {**current, "context": context, "review_digest": digest,
            "display_change": f"Maya context: {current['active_context']} → {context}; native reload saves the run config."}


def enqueue_apply(management, payload):
    reviewed = review(management, payload)
    if payload.get("review_digest") != reviewed["review_digest"]:
        raise OperationsError("Review the exact Maya context change before applying.", 409)
    from services.operations.jobs import enqueue
    return enqueue(management, {"kind": "maya_context", "context": reviewed["context"],
                                **({"drafting": reviewed["drafting"]} if reviewed.get("managed_settings") else {}),
                                "revision": reviewed["revision"], "review_digest": reviewed["review_digest"],
                                "timeout": 1800 if reviewed.get("managed_settings") else 900})


def run(management, specification, folder):
    from services.operations import maya_settings
    if maya_settings.enabled(management):
        return maya_settings.run(management, specification, folder)
    from services.operations.management import api
    reviewed = review(management, specification)
    if specification.get("review_digest") != reviewed["review_digest"]:
        raise OperationsError("The reviewed Maya context change no longer matches.", 409)
    api(management.maya_url, "/api/context", {"max_context": reviewed["context"]})
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        limits = get_json(management.maya_url.rstrip("/") + "/api/context")
        reload = limits.get("reload") or {}
        if reload.get("error"):
            raise OperationsError("Maya context reload failed; inspect native rollback and service logs.", 502)
        live = inventory(management.maya_url)
        if (reload.get("state") not in ("waiting", "running") and live.get("status") == "ok"
                and live.get("model") == reviewed["model"] and live.get("max_context") == reviewed["context"]):
            return {"active_context": reviewed["context"], "model": live["model"]}
        time.sleep(2)
    raise OperationsError("Maya context reload was not verified. Inspect its native reload state before further changes.", 502)
