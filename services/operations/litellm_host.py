"""Fixed-target host operations for the file-backed LiteLLM deployment."""

import contextlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from services.operations.litellm_config import (
    MAX_CONFIG_BYTES, OperationsError, candidate, load_config, review, revision, snapshot,
)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_config(path):
    with path.open("rb") as file:
        raw = file.read(MAX_CONFIG_BYTES + 1)
    load_config(raw)
    return raw


@contextlib.contextmanager
def configuration_lock(path):
    lock_path = path.with_name(path.name + ".odysseus.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+b") as lock:
        if os.name == "nt":
            import msvcrt
            if lock_path.stat().st_size == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise OperationsError("Another configuration change is in progress.", 409) from None
        else:
            import fcntl
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise OperationsError("Another configuration change is in progress.", 409) from None
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def write_config(path, raw):
    # Retain the inode so LiteLLM's existing single-file bind mount sees the edit.
    with path.open("r+b") as file:
        file.seek(0)
        file.write(raw)
        file.truncate()
        file.flush()
        os.fsync(file.fileno())


def docker_command(container, *args):
    from src.host_docker_access import running_in_container, host_docker_access_enabled
    if running_in_container() and not host_docker_access_enabled():
        raise OperationsError("Local host Docker access is disabled. Configure the SSH host helper or explicitly enable host Docker access.", 503)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", container):
        raise OperationsError("The configured LiteLLM container name is invalid.")
    result = subprocess.run(["docker", *args, container], capture_output=True, timeout=60)
    if result.returncode:
        raise OperationsError("The LiteLLM Docker operation failed. Inspect host container logs.", 502)
    return result.stdout + result.stderr if args and args[0] == "logs" else result.stdout


def resolve_secret(value, container):
    if not isinstance(value, str):
        return ""
    if not value.startswith("os.environ/"):
        return value
    name = value.removeprefix("os.environ/")
    if os.environ.get(name):
        return os.environ[name]
    details = json.loads(docker_command(container, "inspect"))[0]
    environment = dict(item.split("=", 1) for item in details["Config"].get("Env", []) if "=" in item)
    if not environment.get(name):
        raise OperationsError("A configured credential environment reference cannot be resolved.")
    return environment[name]


def get_json(url, key=""):
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    opener = build_opener(NoRedirect)
    with opener.open(Request(url, headers=headers), timeout=5) as response:
        raw = response.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise OperationsError("The upstream response exceeds the 1 MiB limit.", 502)
    return json.loads(raw)


def await_gateway(base_url, config, container, wait_seconds=60):
    master_key = resolve_secret(config.get("general_settings", {}).get("master_key", ""), container)
    expected = {row["model_name"] for row in config["model_list"]}
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            get_json(base_url.rstrip("/") + "/health/liveliness")
            catalog = get_json(base_url.rstrip("/") + "/v1/models", master_key)
            advertised = {row.get("id") for row in catalog.get("data", [])}
            if expected.issubset(advertised):
                return
        except (HTTPError, URLError, OSError, ValueError, OperationsError):
            pass
        if time.monotonic() >= deadline:
            raise OperationsError("LiteLLM did not become healthy with the expected model catalog.", 502)
        time.sleep(2)


def probe(raw, draft, index, container):
    _, config = candidate(raw, draft)
    if type(index) is not int or not 0 <= index < len(config["model_list"]):
        raise OperationsError("Choose a deployment to test.")
    params = config["model_list"][index]["litellm_params"]
    base = params.get("api_base", "")
    if not base or base.startswith("os.environ/"):
        raise OperationsError("This endpoint cannot be probed directly.")
    model = params["model"]
    is_ollama = model.startswith(("ollama/", "ollama_chat/"))
    if not is_ollama and not model.startswith("openai/"):
        raise OperationsError("Endpoint probes support Ollama and OpenAI-compatible deployments.")
    expected = model.split("/", 1)[1]
    key = resolve_secret(params.get("api_key", ""), container)
    suffix = "/api/tags" if is_ollama else "/models"
    try:
        data = get_json(base.rstrip("/") + suffix, key)
        names = {row.get("name") for row in data.get("models", [])} if is_ollama else {
            row.get("id") for row in data.get("data", [])}
    except (HTTPError, URLError, OSError, ValueError):
        raise OperationsError("The endpoint probe failed. Check its address, credentials and service status.", 502) from None
    if expected not in names:
        raise OperationsError("The endpoint is reachable but does not advertise the selected backend model.", 422)
    return {"ok": True, "message": "Endpoint reachable; backend model is advertised. No generation was started."}


def apply(path, raw, payload, container, base_url):
    edited, config = candidate(raw, payload.get("draft"))
    if payload.get("review_digest") != revision(edited):
        raise OperationsError("Review these exact changes before applying them.", 409)
    if edited == raw:
        return {"ok": True, "changed": False, **snapshot(raw)}
    backup_dir = path.parent / ".odysseus-backups"
    backup_dir.mkdir(mode=0o700, exist_ok=True)
    backup = backup_dir / (uuid.uuid4().hex + ".yaml")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    try:
        write_config(path, edited)
        docker_command(container, "restart", "--time", "30")
        await_gateway(base_url, config, container)
    except Exception:
        try:
            current = path.read_bytes()
            if current not in (raw, edited):
                raise OperationsError("Configuration changed externally during apply; restore the saved backup manually.", 502)
            write_config(path, raw)
            docker_command(container, "restart", "--time", "30")
            await_gateway(base_url, load_config(raw), container)
        except Exception:
            raise OperationsError("Apply failed and automatic recovery could not be verified. Restore the host backup and inspect LiteLLM.", 502) from None
        raise OperationsError("Apply failed. The previous configuration was restored and LiteLLM recovery was verified.", 502) from None
    return {"ok": True, "changed": True, "backup": backup.name, **snapshot(edited)}


def execute(path, container, base_url, payload):
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise OperationsError("The configured LiteLLM YAML path must be an existing absolute file.", 503)
    action = payload.get("action")
    if action == "snapshot":
        return snapshot(read_config(path))
    with configuration_lock(path):
        raw = read_config(path)
        if payload.get("revision") != revision(raw):
            raise OperationsError("Configuration changed since it was loaded. Refresh before editing.", 409)
        if action == "preview":
            return review(raw, payload.get("draft"))
        if action == "probe":
            return probe(raw, payload.get("draft"), payload.get("index"), container)
        if action == "apply":
            return apply(path, raw, payload, container, base_url)
    raise OperationsError("Unsupported LiteLLM operation.")
