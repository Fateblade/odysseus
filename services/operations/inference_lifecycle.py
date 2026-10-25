"""Cross-process lease shared by routed inference and host mutations."""

from contextlib import contextmanager
from pathlib import Path

from services.operations.litellm_host import configuration_lock
from services.operations.litellm_config import OperationsError


@contextmanager
def runtime_lease(management):
    if not management.path:
        yield
        return
    path = Path(management.path).expanduser().resolve().parent / "inference-runtime"
    path.parent.mkdir(parents=True, exist_ok=True)
    lease = configuration_lock(path)
    try:
        lease.__enter__()
    except OperationsError:
        raise OperationsError("Inference or host maintenance is active. Wait for it to finish before changing model residency.", 409) from None
    try:
        yield
    finally:
        lease.__exit__(None, None, None)


def runtime_busy(management):
    try:
        with runtime_lease(management):
            return False
    except (OperationsError, OSError):
        return True
