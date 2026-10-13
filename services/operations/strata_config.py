"""Review and run allowlisted Strata quantizations and context settings."""

import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

from services.operations.jobs import write_json
from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import configuration_lock, get_json


def variants(management):
    base = management.settings.get("strata_setup")
    if not isinstance(base, dict):
        raise OperationsError("Configure a Strata setup profile on the host first.", 503)
    choices = base.get("models", {base.get("model"): {"config": base.get("config"), "launcher": base.get("launcher")}})
    if not isinstance(choices, dict) or not choices:
        raise OperationsError("Configure the allowed Strata model variants on the host.", 503)
    return base, choices


def service_launcher(management):
    base, choices = variants(management)
    name = base.get("service_launcher")
    if not name:
        return None
    directory = Path(base["directory"]).expanduser().resolve()
    path = directory / name
    if not path.is_absolute() or path.parent.resolve() != directory or path.name in ("", ".", ".."):
        raise OperationsError("The service launcher link must be directly inside the Strata directory.", 503)
    if any(path == directory / choice.get("launcher", "") or path == directory / choice.get("config", "") for choice in choices.values()):
        raise OperationsError("The service launcher link must have a distinct name from the generated model files.", 503)
    return path


def current_model(management):
    base, choices = variants(management)
    link = service_launcher(management)
    if link and link.is_symlink():
        target = link.resolve()
        for model, choice in choices.items():
            if (Path(base["directory"]).expanduser().resolve() / choice["launcher"]).resolve() == target:
                return model
        raise OperationsError("The Strata service launcher points outside the allowed model variants.", 503)
    return base["model"]


def profile(management, model=None, require_existing=True):
    base, choices = variants(management)
    model = current_model(management) if model is None else model
    if not isinstance(model, str):
        raise OperationsError("Select a Strata quantization configured in the host allowlist.")
    if model not in choices or not isinstance(choices[model], dict):
        raise OperationsError("Select a Strata quantization configured in the host allowlist.")
    value = {**base, "model": model, "config": choices[model].get("config"), "launcher": choices[model].get("launcher")}
    directory = Path(value.get("directory", "")).expanduser().resolve()
    config = (directory / value.get("config", "")).resolve()
    launcher = (directory / value.get("launcher", "")).resolve()
    if not directory.is_dir() or not (directory / "setup.sh").is_file() or (require_existing and not config.is_file()):
        raise OperationsError("Strata setup directory or model configuration does not exist.", 503)
    if not config.is_relative_to(directory) or config.suffix != ".json" or not launcher.is_relative_to(directory) or launcher.suffix != ".sh":
        raise OperationsError("Strata config and launcher must be inside its setup directory.", 503)
    for key in ("family", "model", "backend"):
        text = value.get(key)
        if not isinstance(text, str) or not text or len(text) > 80 or text.startswith("-") or not all(c.isalnum() or c in "_-" for c in text):
            raise OperationsError("Strata family, model or backend profile is invalid.", 503)
    data_dir = value.get("data_directory")
    if not isinstance(data_dir, str) or not Path(data_dir).is_absolute():
        raise OperationsError("Configure an absolute Strata model data directory.", 503)
    port = value.get("port", 8081)
    if type(port) != int or not 1 <= port <= 65535:
        raise OperationsError("Strata setup port is invalid.", 503)
    return value, directory, config, launcher


def configured_context(raw):
    config = json.loads(raw.decode("utf-8-sig"))
    args = config.get("args", [])
    found = []
    for index, arg in enumerate(args):
        if arg == "--max-context" and index + 1 < len(args):
            found.append(int(args[index + 1]))
        elif isinstance(arg, str) and arg.startswith("--max-context="):
            found.append(int(arg.split("=", 1)[1]))
    if len(found) != 1 or found[0] <= 0:
        raise OperationsError("The Strata config must contain exactly one valid --max-context argument.")
    return found[0]


def fingerprint(management):
    value, directory, config, launcher = profile(management)
    service = management.settings.get("services", {}).get(value.get("service", "strata"))
    digest = hashlib.sha256(json.dumps({"profile": value, "service": service, "url": management.strata_url}, sort_keys=True).encode())
    paths = {config, launcher, directory / "setup.sh", directory / "setup.py"}
    _, choices = variants(management)
    for model in choices:
        _, _, model_config, model_launcher = profile(management, model, require_existing=False)
        paths.update((model_config, model_launcher))
    link = service_launcher(management)
    digest.update((os.readlink(link) if link and link.is_symlink() else "no service launcher link").encode())
    for path in sorted(paths):
        digest.update(str(path).encode())
        digest.update(path.read_bytes() if path.exists() else b"missing")
    return digest.hexdigest()


def snapshot(management):
    try:
        value, _, config, _ = profile(management)
    except OperationsError as error:
        return {"configured": False, "message": str(error)}
    raw = config.read_bytes()
    if len(raw) > 1048576:
        raise OperationsError("Strata configuration exceeds 1 MiB.")
    active, active_model = None, None
    try:
        health = get_json(management.strata_url.rstrip("/") + "/health")
        active, active_model = health.get("max_context"), health.get("model")
    except Exception:
        pass
    service = value.get("service", "strata")
    link = service_launcher(management)
    return {"configured": True, "revision": fingerprint(management), "configured_context": configured_context(raw),
            "active_context": active, "family": value["family"], "model": value["model"], "backend": value["backend"],
            "active_model": active_model, "models": [{"name": model, "installed": profile(management, model, False)[2].is_file()} for model in variants(management)[1]],
            "port": value.get("port", 8081), "data_directory": value["data_directory"],
            "launcher": value["launcher"], "switch_available": bool(link and link.is_symlink()),
            "restart_available": service in management.settings.get("services", {})}


def review(management, payload):
    value, directory, _, _ = profile(management, payload.get("model"), require_existing=False)
    context = payload.get("context")
    maximum = value.get("maximum_context", 1048576)
    if type(context) != int or type(maximum) != int or not 1024 <= context <= maximum:
        raise OperationsError(f"Context must be a whole number between 1024 and {maximum} tokens.")
    revision = fingerprint(management)
    if payload.get("revision") != revision:
        raise OperationsError("Strata configuration or setup files changed. Reload before reviewing.", 409)
    command = ["./setup.sh", "--setup", "--yes", "--family", value["family"], "--model", value["model"],
               "--context", str(context), "--backend", value["backend"], "--data-dir", value["data_directory"],
               "--port", str(value.get("port", 8081)), "--no-start"]
    digest = hashlib.sha256(json.dumps({"revision": revision, "command": command}, sort_keys=True).encode()).hexdigest()
    return {"revision": revision, "review_digest": digest, "context": context, "model": value["model"], "command": command,
            "display_command": shlex.join(command), "directory": str(directory), "restart_required": True}


def enqueue_apply(management, payload):
    reviewed = review(management, payload)
    if payload.get("review_digest") != reviewed["review_digest"]:
        raise OperationsError("Review the exact Strata setup command before applying.", 409)
    value, _, _, _ = profile(management)
    if value.get("service", "strata") not in management.settings.get("services", {}):
        raise OperationsError("Configure a Strata service using the generated launcher before applying setup.", 503)
    link = service_launcher(management)
    if reviewed["model"] != current_model(management) and (not link or not link.is_symlink()):
        raise OperationsError("Configure the service launcher symlink before switching quantizations.", 503)
    from services.operations.jobs import enqueue
    return enqueue(management, {"kind": "strata_setup", "context": reviewed["context"], "model": reviewed["model"], "revision": reviewed["revision"],
                                "review_digest": reviewed["review_digest"], "timeout": 7200})


def await_context(management, context, timeout=300, model=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = get_json(management.strata_url.rstrip("/") + "/health")
            if health.get("status") == "ok" and health.get("max_context") == context and (model is None or health.get("model") == model):
                return
        except Exception:
            pass
        time.sleep(2)
    raise OperationsError("Strata did not report the requested active context after restart.", 502)


def run(management, specification, folder):
    reviewed = review(management, specification)
    if reviewed["review_digest"] != specification["review_digest"]:
        raise OperationsError("The reviewed Strata setup command changed.", 409)
    value, directory, config, launcher = profile(management, specification.get("model"), require_existing=False)
    _, _, previous_config, previous_launcher = profile(management)
    link = service_launcher(management)
    old_link = os.readlink(link) if link and link.is_symlink() else None
    service = value.get("service", "strata")
    def cancelled(*_):
        raise InterruptedError("Setup cancelled")
    previous_handler = signal.signal(signal.SIGTERM, cancelled)
    try:
        with configuration_lock(directory / ".odysseus-strata-setup"):
            if fingerprint(management) != reviewed["revision"]:
                raise OperationsError("Strata files changed before the setup job started.", 409)
            # Never stop a session-owned server through an unrelated service.
            management.service(service, "is-active", check=True)
            unit_command = management.service(service, "show").stdout.decode(errors="replace")
            if str(link or previous_launcher) not in unit_command:
                raise OperationsError("The configured Strata service must execute the generated launcher.", 503)
            if link and (not link.is_symlink() or link.resolve() != previous_launcher):
                raise OperationsError("The Strata service launcher must be a symlink to the current allowed model launcher.", 503)
            metrics = get_json(management.strata_url.rstrip("/") + "/metrics")
            live = metrics.get("live", {})
            if live.get("queued") or live.get("state") not in ("idle", "unloaded", "ready"):
                raise OperationsError("Strata is busy or its idle state cannot be verified. Retry when idle.", 409)
            backup = folder / "strata-backup"
            backup.mkdir(mode=0o700)
            originals = {path: path.read_bytes() if path.exists() else None for path in (config, launcher, previous_config, previous_launcher)}
            old_context = configured_context(originals[previous_config])
            old_model_name = json.loads(originals[previous_config].decode("utf-8-sig")).get("model_name")
            for index, (path, raw) in enumerate(originals.items()):
                if raw is not None:
                    with os.fdopen(os.open(backup / str(index), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
                        file.write(raw)
            write_json(backup / "manifest.json", {str(path): str(index) if raw is not None else None for index, (path, raw) in enumerate(originals.items())})
            write_json(backup / "launcher-link.json", {"path": str(link) if link else None, "target": old_link})
            stopped = False
            try:
                print("Stopping managed Strata server before setup", flush=True)
                stopped = True
                management.service(service, "stop")
                try:
                    get_json(management.strata_url.rstrip("/") + "/health")
                except Exception:
                    pass
                else:
                    raise OperationsError("Another Strata server still answers after the managed service stopped.", 409)
                print(reviewed["display_command"], flush=True)
                completed = subprocess.run(reviewed["command"], cwd=directory, stdin=subprocess.DEVNULL)
                if completed.returncode:
                    raise OperationsError("Strata setup failed. See the job output.", 502)
                generated = configured_context(config.read_bytes())
                if generated != specification["context"] or not launcher.is_file():
                    raise OperationsError("Generated configuration or launcher does not match the reviewed context.", 502)
                if link:
                    switch_launcher(link, launcher)
                model_name = json.loads(config.read_text(encoding="utf-8-sig")).get("model_name")
                if not isinstance(model_name, str) or not model_name:
                    raise OperationsError("Generated Strata config has no model identity to verify.", 502)
                print("Setup completed; starting the service using its configured generated launcher", flush=True)
                management.service(service, "start")
                await_context(management, generated, model=model_name)
                return {"model": value["model"], "active_model": model_name, "configured_context": generated,
                        "active_context": generated, "launcher": value["launcher"], "backup": str(backup)}
            except Exception:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                restored = False
                try:
                    if stopped:
                        management.service(service, "stop")
                        for path, raw in originals.items():
                            if raw is None:
                                path.unlink(missing_ok=True)
                            else:
                                with path.open("wb") as file:
                                    file.write(raw)
                                    file.flush()
                                    os.fsync(file.fileno())
                        if link and old_link is not None:
                            switch_launcher(link, old_link)
                        management.service(service, "start")
                        await_context(management, old_context, model=old_model_name)
                        restored = True
                finally:
                    write_json(folder / "result.json", {"restored": restored, "backup": str(backup),
                               "note": "Recovery covers config and launcher; setup downloads/builds are not rolled back."})
                    print("Previous config restored and context verified" if restored else "Recovery not verified; inspect the host and saved backup", flush=True)
                raise
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def switch_launcher(link, target):
    import uuid
    temporary = link.with_name(link.name + "." + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)
