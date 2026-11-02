"""Bounded read-only discovery of configured native providers and router mappings."""

import json
import time
from urllib.request import Request, build_opener

from services.operations.litellm_config import MAX_CONFIG_BYTES, OperationsError, public_url, UNCHANGED
from services.operations.litellm_host import NoRedirect, get_json
from services.operations.model_metadata import digest, validate

BACKENDS = ("ollama", "strata", "dwarfstar", "maya", "maya_glm", "strata_ds41f")


def show_ollama(base, model):
    request = Request(base.rstrip("/") + "/api/show", data=json.dumps({"model": model}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(NoRedirect).open(request, timeout=5) as response:
        raw = response.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise OperationsError("Ollama metadata exceeds 1 MiB.", 502)
    return json.loads(raw)


def router_configuration(management):
    from services.operations.inference_settings import location, read
    path = location(management)
    if path and path.exists():
        return read(path)[1]
    return {}


def collect(management):
    from services.operations import overview, maya, dwarfstar, strata_ds41f
    router = router_configuration(management)
    sources, providers = {}, []
    probes = {"ollama": overview.ollama, "strata": overview.strata, "dwarfstar": dwarfstar.inventory,
              "maya": maya.inventory, "maya_glm": maya.glm_inventory, "strata_ds41f": strata_ds41f.inventory}
    now = time.time()
    for backend in BACKENDS:
        base = router.get("backends", {}).get(backend) or getattr(management, backend + "_url", "")
        if not base:
            continue
        if public_url(base) == UNCHANGED:
            providers.append({"id": backend, "status": "invalid_endpoint", "last_attempt_at": now})
            continue
        try:
            live = probes[backend](base)
            models = live.get("installed", []) if backend == "ollama" else [
                {"name": live.get("model") or live.get("configured_model"), "context_length": live.get("max_context")}]
            known_names = {item.get("name") for item in models}
            for route in router.get("routes", {}).values():
                if route.get("backend") == backend and route.get("model") not in known_names:
                    models.append({"name": route["model"]})
                    known_names.add(route["model"])
            provider = {"id": backend, "status": live.get("status", "unknown"), "last_attempt_at": now}
            providers.append(provider)
            for model in models[:50]:
                name = model.get("name")
                if not isinstance(name, str) or not name:
                    continue
                detected, details = {}, {"digest": model.get("digest")}
                if backend == "ollama":
                    try:
                        shown = show_ollama(base, name)
                        capabilities = shown.get("capabilities", [])
                        if isinstance(capabilities, list) and capabilities:
                            for capability, field in (("tools", "supports_function_calling"), ("vision", "supports_vision"), ("thinking", "supports_reasoning")):
                                if capability in capabilities:
                                    detected[field] = True
                            if "embedding" in capabilities:
                                detected["mode"] = "embedding"
                            elif "completion" in capabilities:
                                detected["mode"] = "chat"
                        for key, value in shown.get("model_info", {}).items():
                            if key.endswith(".context_length") and type(value) is int and value > 0:
                                details["published_context"] = value
                    except (OSError, ValueError, OperationsError):
                        details["show_unavailable"] = True
                    loaded = next((item for item in live.get("loaded", []) if item.get("name") == name), {})
                    context = loaded.get("context_length")
                else:
                    context = model.get("context_length")
                if type(context) is int and context > 0:
                    detected["context_window"] = validate("context_window", context)
                    details["active_context"] = context
                # Use an existing router route whenever this model is coordinated there.
                routes = [(alias, route) for alias, route in router.get("routes", {}).items()
                          if route.get("backend") == backend and route.get("model") == name]
                if routes:
                    mappings = [("openai/" + alias, f"http://127.0.0.1:{router.get('port', 8094)}/v1") for alias, _ in routes]
                elif backend in router.get("backends", {}):
                    # New coordinated routes need explicit host configuration before publication.
                    provider["unrouted_models"] = provider.get("unrouted_models", 0) + 1
                    continue
                elif backend == "ollama":
                    prefix = "ollama/" if detected.get("mode") == "embedding" else "ollama_chat/"
                    mappings = [(prefix + name, base.rstrip("/"))]
                else:
                    mappings = [("openai/" + name, base.rstrip("/") + "/v1")]
                for model_name, api_base in mappings:
                    source_id = digest([backend, name, model.get("digest"), model_name, api_base, management.settings.get(backend + "_setup", {})])[:32]
                    sources[source_id] = {"id": source_id, "provider": backend, "name": name,
                                          "model": model_name, "api_base": api_base, "detected": detected,
                                          "details": details, "observed_at": now, "last_success_at": now,
                                          "last_attempt_at": now, "status": "fresh"}
        except (OSError, ValueError, TypeError, OperationsError):
            providers.append({"id": backend, "status": "unavailable", "last_attempt_at": now})
    return sources, providers
