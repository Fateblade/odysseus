"""Confirmed process state, independent from HTTP readiness or model residency."""

from services.operations.litellm_config import OperationsError


def require_transition(state, operation):
    expected = "stopped" if operation == "start" else "running"
    if state.get("state") != expected:
        raise OperationsError(f"Cannot {operation}: the target is {state.get('state', 'unknown')}. Refresh its state before retrying.", 409)


def require_strata_transition(base_url, operation):
    from services.operations.management import api
    health = api(base_url, "/health", {}, method="GET", timeout=5)
    loaded = health.get("loaded")
    expected = operation == "unload"
    if health.get("status") != "ok" or not isinstance(loaded, bool) or loaded != expected:
        state = "loaded" if loaded is True else "unloaded" if loaded is False else "unknown"
        raise OperationsError(f"Cannot {operation}: Strata model is {state}. Refresh its state before retrying.", 409)
