"""Read-only Project Maya v1.1.22 health and model discovery."""

import time

from services.operations.litellm_host import get_json


def run_service(management, specification):
    from services.operations.litellm_config import OperationsError
    operation = specification.get("operation")
    if operation not in ("start", "stop", "restart"):
        raise OperationsError("Unknown Maya service operation.")
    if operation in ("start", "restart"):
        live = management.residency()
        if (live["strata"].get("loaded") or live["ollama"].get("loaded")
                or live["dwarfstar"].get("loaded")):
            raise OperationsError("Unload competing Strata/Ollama models and stop DwarfStar before starting Maya.", 409)
    result = management.service("maya", operation)
    if operation in ("start", "restart"):
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            if inventory(management.maya_url).get("status") == "ok":
                break
            management.service("maya", "status")
            time.sleep(2)
        else:
            raise OperationsError("Maya startup readiness was not verified. Inspect service logs and memory before another job.", 502)
    elif inventory(management.maya_url).get("status") == "ok":
        raise OperationsError("Maya still answers after the managed service stopped. Inspect other server processes.", 409)
    return {"status": "ok", "operation": operation,
            "log": (result.stdout + result.stderr)[-65536:].decode(errors="replace"),
            "note": "Readiness verified" if operation != "stop" else "Managed service stopped"}


def inventory(base_url):
    if not base_url:
        return {"status": "disabled", "configured": False,
                "message": "Configure ODYSSEUS_OPERATIONS_MAYA_URL to monitor Maya."}
    try:
        base = base_url.rstrip("/")
        health = get_json(base + "/health")
        catalog = get_json(base + "/v1/models")
        rows = catalog.get("data") if isinstance(catalog, dict) else None
        if (not isinstance(health, dict) or health.get("status") not in ("ok", "reloading", "updating")
                or type(health.get("unloaded")) is not bool or not isinstance(rows, list)
                or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                       or not row["id"].strip() for row in rows)):
            raise ValueError()
        loaded = [row for row in rows if row.get("status", {}).get("value") == "loaded"]
        ready = health["status"] == "ok" and not health["unloaded"] and len(loaded) == 1
        context = loaded[0].get("meta", {}).get("n_ctx") if ready else None
        if ready and (health.get("model") != loaded[0]["id"] or type(context) is not int or context <= 0
                      or health.get("max_context") != context):
            raise ValueError()
        return {"configured": True, "status": "ok" if ready else "unloaded" if health["unloaded"] else "degraded",
                "loaded": True if ready else False if health["unloaded"] else None,
                "model": loaded[0]["id"] if ready else health.get("model"),
                "aliases": [row["id"] for row in loaded] if ready else [],
                "max_context": context if type(context) is int and context > 0 else None,
                "version": health.get("version"), "state": health["status"],
                "message": "Maya readiness and runtime context; physical residency requires host memory and SSD measurements."}
    except Exception:
        return {"status": "unavailable", "configured": True,
                "message": "Maya is unreachable or returned an invalid health/catalog response. Inspect its service logs."}
