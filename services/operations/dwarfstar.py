"""Read-only DwarfStar model discovery without triggering inference."""

from services.operations.litellm_host import get_json


def inventory(base_url, management=None):
    if not base_url:
        return {"status": "disabled", "configured": False,
                "message": "Configure ODYSSEUS_OPERATIONS_DWARFSTAR_URL to monitor DwarfStar."}
    try:
        data = get_json(base_url.rstrip("/") + "/v1/models")
        rows = data.get("data") if isinstance(data, dict) else None
        if (not isinstance(rows, list) or not rows
                or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                       or not row["id"].strip() for row in rows)):
            raise ValueError()
        names = {row["name"] for row in rows if isinstance(row.get("name"), str) and row["name"].strip()}
        contexts = {row["context_length"] for row in rows
                    if type(row.get("context_length")) is int and row["context_length"] > 0}
        return {"status": "ok", "configured": True, "loaded": True,
                "model": next(iter(names)) if len(names) == 1 else None,
                "max_context": next(iter(contexts)) if len(contexts) == 1 else None,
                "aliases": list(dict.fromkeys(row["id"] for row in rows)),
                "message": "Aliases refer to one startup-selected model. Allocation and request activity are not reported by this API."}
    except Exception:
        if management is not None:
            try:
                state = management.service("dwarfstar", "is-active", check=False)
                if state.returncode == 3 and state.stdout.decode().strip() == "inactive":
                    return {"status": "stopped", "configured": True, "loaded": False,
                            "message": "DwarfStar service is stopped. Start it from Management when needed."}
            except Exception:
                pass
        return {"status": "unavailable", "configured": True,
                "message": "DwarfStar is stopped, starting, unreachable or returned an invalid model catalog. Check its service logs."}
