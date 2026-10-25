"""Confirmed process state, independent from HTTP readiness or model residency."""

from services.operations.litellm_config import OperationsError


def require_transition(state, operation):
    expected = "stopped" if operation == "start" else "running"
    if state.get("state") != expected:
        raise OperationsError(f"Cannot {operation}: the target is {state.get('state', 'unknown')}. Refresh its state before retrying.", 409)
