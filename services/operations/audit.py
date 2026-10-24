"""Private, rotating request journal shared by the host and LiteLLM callback."""

import contextlib
import json
import logging
import os
import time
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_THREAD_LOCK = threading.RLock()

MAX_BYTES = 20 * 1024 * 1024
BACKUPS = 4
CONTENT_BYTES = 2 * 1024 * 1024
FIELDS = {"source", "request_id", "job_id", "actor", "client_ip", "client_label", "user_agent", "key_alias",
          "claimed_user", "client_request_id", "action", "backend", "model", "requested_model", "endpoint",
          "status", "status_code", "error_type", "duration_ms", "prompt_tokens", "output_tokens", "cache_hit",
          "stream", "context", "operation", "name", "destination", "previous", "current", "call_type", "content"}


def open_private(path, flags):
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        uid, gid = os.environ.get("ODYSSEUS_AUDIT_OWNER_UID"), os.environ.get("ODYSSEUS_AUDIT_OWNER_GID")
        if uid and gid and uid.isdigit() and gid.isdigit() and hasattr(os, "fchown"):
            os.fchown(fd, int(uid), int(gid))
    except Exception:
        os.close(fd)
        raise
    return fd


def directory(management):
    return Path(management.settings.get("audit_directory") or os.environ.get("ODYSSEUS_AUDIT_DIRECTORY")
                or Path.home() / ".local/share/odysseus-operations/audit").expanduser()


def endpoint(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if not host or parsed.scheme not in ("http", "https"):
            return None
        host = f"[{host}]" if ":" in host else host
        authority = f"{host}:{parsed.port}" if parsed.port else host
        return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
    except (ValueError, TypeError):
        return None


@contextlib.contextmanager
def lock(folder):
    with _THREAD_LOCK:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(open_private(folder / ".audit.lock", os.O_RDWR | os.O_CREAT), "r+b") as file:
            if os.name == "nt":
                import msvcrt
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)

def content(value):
    encoded = json.dumps(value, ensure_ascii=True, default=str)
    if len(encoded) <= CONTENT_BYTES:
        return value
    return {"truncated": True, "captured_text": encoded[:CONTENT_BYTES], "original_json_bytes": len(encoded)}


def emit(folder, event, **values):
    record = {"id": uuid.uuid4().hex, "time": time.time(), "event": str(event)[:100]}
    for key, value in values.items():
        if key not in FIELDS or value is None:
            continue
        if key == "endpoint":
            record[key] = endpoint(value)
        elif key == "content":
            record[key] = content(value)
        elif isinstance(value, (str, int, float, bool)):
            record[key] = value[:512] if isinstance(value, str) else value
        elif key in ("previous", "current"):
            record[key] = content(value)
    raw = (json.dumps(record, ensure_ascii=True, default=str) + "\n").encode()
    folder = Path(folder)
    try:
        with lock(folder):
            path = folder / "events.jsonl"
            if path.exists() and path.stat().st_size + len(raw) > MAX_BYTES:
                (folder / f"events.jsonl.{BACKUPS}").unlink(missing_ok=True)
                for index in range(BACKUPS - 1, 0, -1):
                    old = folder / f"events.jsonl.{index}"
                    if old.exists():
                        os.replace(old, folder / f"events.jsonl.{index + 1}")
                os.replace(path, folder / "events.jsonl.1")
            with os.fdopen(open_private(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT), "ab") as file:
                file.write(raw)
        return record["id"]
    except OSError:
        logging.getLogger(__name__).warning("Request audit could not be written; check the private audit directory permissions and disk space.")
        return None


def read(folder, event_id=None, limit=200):
    folder = Path(folder)
    if not folder.is_dir():
        return {"status": "empty", "events": [], "message": "No journal entries yet. Check gateway logging and the residency observer setup."}
    records = []
    damaged = 0
    with lock(folder):
        for index in range(BACKUPS + 1):
            path = folder / ("events.jsonl" if index == 0 else f"events.jsonl.{index}")
            if not path.exists():
                continue
            with path.open("rb") as file:
                for line in file:
                    try:
                        record = json.loads(line)
                        if not isinstance(record, dict) or not isinstance(record.get("time"), (int, float)):
                            raise ValueError("Invalid journal record")
                        if event_id:
                            if record.get("id") == event_id:
                                return {"event": record}
                        else:
                            record.pop("content", None)
                            records.append(record)
                            if len(records) > limit * 2:
                                records = sorted(records, key=lambda item: item["time"], reverse=True)[:limit]
                    except (ValueError, KeyError):
                        damaged += 1
            if not event_id and len(records) >= limit:
                break
    if event_id:
        return {"event": None, "message": "Entry is absent or has rotated out."}
    return {"status": "ok", "events": sorted(records, key=lambda item: item["time"], reverse=True)[:limit],
            "skipped_lines": damaged, "message": "Gateway and host actions are attributable; observed residency changes alone do not establish a caller."}


def request_fields(payload):
    fields = {key: payload.get(key) for key in ("backend", "model", "context", "operation", "name", "destination")}
    fields["action"] = payload.get("action") or payload.get("kind")
    if payload.get("target"):
        fields["backend"] = payload["name"] if payload["target"] == "service" and payload.get("name") in ("maya", "maya_glm") else payload["target"]
    elif str(payload.get("action", "")).startswith("maya_glm_"):
        fields["backend"] = "maya_glm"
    return fields


def run_operation(management, payload, operation):
    actor = payload.get("_audit", {})
    actor = actor if isinstance(actor, dict) else {}
    management.audit_actor = {key: actor.get(key) for key in ("actor", "client_ip", "user_agent", "request_id")}
    changes = {"manage", "apply", "job_start", "job_cancel", "script_save", "strata_apply", "dwarfstar_apply", "maya_apply", "maya_glm_apply", "measurement_import"}
    if payload.get("action") not in changes:
        return operation()
    fields = {**request_fields(payload), **management.audit_actor, "source": "operations"}
    emit(directory(management), "operation.requested", **fields)
    try:
        result = operation()
    except Exception as error:
        emit(directory(management), "operation.failed", **fields, error_type=type(error).__name__,
             status_code=getattr(error, "status", None))
        raise
    emit(directory(management), "operation.accepted" if isinstance(result, dict) and result.get("job") else "operation.completed",
         **fields, job_id=result.get("job", {}).get("id") if isinstance(result, dict) else None)
    return result
