"""Prevent dashboard model loads from overlapping exclusive inference engines."""

from services.operations.litellm_config import OperationsError

EXCLUSIVE = ("maya", "maya_glm", "dwarfstar")


def ensure_available(management, backend):
    if backend not in EXCLUSIVE and not any(getattr(management, name + "_url", "") for name in EXCLUSIVE):
        return
    live = management.residency()
    if backend in EXCLUSIVE and (live.get("strata", {}).get("loaded") or live.get("ollama", {}).get("loaded")):
        raise OperationsError("Unload competing Strata/Ollama models before starting an exclusive engine.", 409)
    for other in EXCLUSIVE:
        if other == backend:
            continue
        state = live.get(other, {})
        if state.get("loaded"):
            message = "Stop the other Maya profile" if backend in ("maya", "maya_glm") and other in ("maya", "maya_glm") else f"Stop {other}"
            raise OperationsError(message + " before loading this model. Models are never stopped automatically.", 409)
        if state.get("configured") and state.get("status") not in ("ok", "unloaded", "disabled", "stopped"):
            result = management.service(other, "is-active", check=False)
            if result.returncode != 3 or result.stdout.decode().strip() != "inactive":
                raise OperationsError(f"{other} is not confirmed stopped. Inspect its service before loading another model.", 409)
