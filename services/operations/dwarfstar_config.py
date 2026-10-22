"""Reviewed context changes for an allowlisted DwarfStar user service."""

import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import time
import uuid
from pathlib import Path

from services.operations.dwarfstar import inventory
from services.operations.jobs import write_json
from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import configuration_lock


def systemctl(*arguments):
    result = subprocess.run(["systemctl", "--user", *arguments], capture_output=True, timeout=180)
    if result.returncode:
        raise OperationsError("DwarfStar service operation failed. Inspect its user-service logs and helper permissions.", 502)
    return result.stdout.decode(errors="replace")


def context_argument(raw):
    try:
        text = raw.decode("utf-8")
        sections = list(re.finditer(r"(?ms)^\[Service\][ \t]*\r?\n(.*?)(?=^\[|\Z)", text))
        if len(sections) != 1:
            raise ValueError()
        section = sections[0]
        commands = list(re.finditer(r"(?m)^[ \t]*ExecStart[ \t]*=[ \t]*([^\r\n]+)", section[1]))
        if len(commands) != 1:
            raise ValueError()
        command = commands[0][1]
        args = shlex.split(command)
        if not args or Path(args[0]).name != "ds4-server" or "\\" in command or "$" in command:
            raise ValueError()
        count = sum(arg in ("--ctx", "-c") or arg.startswith("--ctx=") for arg in args)
        matches = list(re.finditer(r"(?<!\S)(?:--ctx|-c)(?:[ \t]+|=)([0-9]+)(?=\s|$)", command))
        if count != 1 or len(matches) != 1 or int(matches[0][1]) <= 0:
            raise ValueError()
        offset = section.start(1) + commands[0].start(1)
        return text, int(matches[0][1]), offset + matches[0].start(1), offset + matches[0].end(1)
    except (UnicodeError, ValueError):
        raise OperationsError("The DwarfStar unit needs one direct ds4-server ExecStart with a single unquoted --ctx integer; launcher scripts and continued commands are unsupported.", 503) from None


def profile(management):
    setup = management.settings.get("dwarfstar_setup", {})
    if not isinstance(setup, dict) or not management.dwarfstar_url:
        raise OperationsError("Configure the DwarfStar endpoint and user service on the host first.", 503)
    service = setup.get("service", "dwarfstar")
    setting = management.settings.get("services", {}).get(service)
    setting = {"unit": setting} if isinstance(setting, str) else setting or {}
    unit = setting.get("unit")
    if (not isinstance(unit, str) or not re.fullmatch(r"[\w][\w.@-]*\.service", unit)
            or setting.get("scope", "user") != "user" or setting.get("sudo")):
        raise OperationsError("Context management requires an allowlisted DwarfStar user service without sudo.", 503)
    path = Path(setup.get("unit_file", Path.home() / ".config/systemd/user" / unit)).expanduser()
    if not path.is_absolute() or path.name != unit or path.is_symlink() or not path.is_file():
        raise OperationsError("The configured DwarfStar user-unit file must exist and must not be a symlink.", 503)
    raw = path.read_bytes()
    if len(raw) > 131072:
        raise OperationsError("DwarfStar unit exceeds 128 KiB.", 503)
    _, context, _, _ = context_argument(raw)
    maximum = setup.get("maximum_context", 262144)
    if type(maximum) != int or not 1024 <= maximum <= 1048576:
        raise OperationsError("DwarfStar maximum_context must be between 1024 and 1048576 tokens.", 503)
    return {"service": service, "unit": unit, "path": path, "raw": raw,
            "context": context, "maximum": maximum, "setup": setup, "setting": setting}


def service_state(value):
    output = systemctl("show", value["unit"], "--all", "--property=FragmentPath,DropInPaths,ActiveState,NeedDaemonReload")
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if (fields.get("FragmentPath") != str(value["path"]) or fields.get("DropInPaths") != ""
            or fields.get("NeedDaemonReload") != "no"):
        raise OperationsError("DwarfStar must use the configured unit file without drop-ins or pending daemon reloads.", 409)
    if fields.get("ActiveState") not in ("active", "inactive", "failed"):
        raise OperationsError("DwarfStar is changing service state. Wait before applying context.", 409)
    return fields["ActiveState"] == "active"


def fingerprint(management, value):
    data = {"setup": value["setup"], "service": value["setting"],
            "path": str(value["path"]), "url": management.dwarfstar_url}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode() + b"\0" + value["raw"]).hexdigest()


def snapshot(management):
    try:
        value = profile(management)
        running = service_state(value)
    except (OperationsError, OSError) as error:
        return {"configured": False, "message": str(error)}
    live = inventory(management.dwarfstar_url)
    return {"configured": True, "revision": fingerprint(management, value), "unit": value["unit"],
            "configured_context": value["context"], "active_context": live.get("max_context"),
            "model": live.get("model"), "running": running, "maximum_context": value["maximum"]}


def review(management, payload):
    value = profile(management)
    context = payload.get("context")
    if type(context) != int or not 1024 <= context <= value["maximum"]:
        raise OperationsError(f"Context must be a whole number between 1024 and {value['maximum']} tokens.")
    revision = fingerprint(management, value)
    if payload.get("revision") != revision:
        raise OperationsError("DwarfStar configuration changed. Reload before reviewing.", 409)
    text, old_context, start, end = context_argument(value["raw"])
    replacement = (text[:start] + str(context) + text[end:]).encode()
    digest = hashlib.sha256(revision.encode() + b"\0" + replacement).hexdigest()
    return {"revision": revision, "review_digest": digest, "context": context, "previous_context": old_context,
            "unit": value["unit"], "restart_required": True,
            "display_change": f"{value['unit']}: --ctx {old_context} → --ctx {context}"}


def enqueue_apply(management, payload):
    reviewed = review(management, payload)
    if payload.get("review_digest") != reviewed["review_digest"]:
        raise OperationsError("Review the exact DwarfStar context change before applying.", 409)
    service_state(profile(management))
    from services.operations.jobs import enqueue
    return enqueue(management, {"kind": "dwarfstar_context", "context": reviewed["context"],
                                "revision": reviewed["revision"], "review_digest": reviewed["review_digest"], "timeout": 1800})


def replace_unit(path, raw, mode):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def await_context(management, context, model, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = inventory(management.dwarfstar_url)
        if live.get("status") == "ok" and live.get("max_context") == context and live.get("model") == model:
            return
        time.sleep(2)
    raise OperationsError("DwarfStar did not report the requested model and context after startup.", 502)


def run(management, specification, folder):
    value = profile(management)
    with configuration_lock(value["path"]):
        reviewed = review(management, specification)
        if reviewed["review_digest"] != specification.get("review_digest"):
            raise OperationsError("The reviewed DwarfStar context change no longer matches.", 409)
        value = profile(management)
        if fingerprint(management, value) != reviewed["revision"]:
            raise OperationsError("DwarfStar unit changed before the job started.", 409)
        running = service_state(value)
        live = inventory(management.dwarfstar_url)
        model = live.get("model")
        if running and (live.get("status") != "ok" or live.get("max_context") != value["context"] or not model):
            raise OperationsError("The managed DwarfStar service must be ready and report its configured context before restarting.", 409)
        if not running and live.get("status") == "ok":
            raise OperationsError("A DwarfStar server answers outside the configured active service. Stop it before changing context.", 409)
        text, _, start, end = context_argument(value["raw"])
        replacement = (text[:start] + str(reviewed["context"]) + text[end:]).encode()
        backup = folder / "dwarfstar-backup"
        backup.mkdir(mode=0o700)
        mode = value["path"].stat().st_mode & 0o777
        replace_unit(backup / value["unit"], value["raw"], 0o600)
        write_json(backup / "manifest.json", {"unit_file": str(value["path"]), "running": running,
                                              "previous_context": value["context"], "model": model})
        stopped, written = False, False

        def cancelled(*_):
            raise InterruptedError("DwarfStar context change cancelled")

        previous_handler = signal.signal(signal.SIGTERM, cancelled)
        try:
            if running:
                print("Stopping managed DwarfStar; active requests will be interrupted", flush=True)
                stopped = True
                systemctl("stop", value["unit"])
            if inventory(management.dwarfstar_url).get("status") == "ok":
                raise OperationsError("Another DwarfStar server still answers after the service stopped.", 409)
            if fingerprint(management, profile(management)) != reviewed["revision"]:
                raise OperationsError("DwarfStar unit changed during the operation.", 409)
            replace_unit(value["path"], replacement, mode)
            written = True
            systemctl("daemon-reload")
            print(reviewed["display_change"], flush=True)
            if running:
                systemctl("start", value["unit"])
                await_context(management, reviewed["context"], model)
            return {"configured_context": reviewed["context"], "active_context": reviewed["context"] if running else None,
                    "running": running, "backup": str(backup)}
        except Exception:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            restored = False
            try:
                expected = replacement if written else value["raw"]
                if value["path"].is_symlink() or value["path"].read_bytes() != expected:
                    raise OperationsError("DwarfStar unit was edited externally; automatic recovery will not overwrite it.", 409)
                if stopped or written:
                    systemctl("stop", value["unit"])
                    replace_unit(value["path"], value["raw"], mode)
                    systemctl("daemon-reload")
                    if running:
                        systemctl("start", value["unit"])
                        await_context(management, value["context"], model)
                restored = True
            finally:
                write_json(folder / "result.json", {"restored": restored, "backup": str(backup)})
                print("Previous context and service state restored" if restored else "Recovery unverified; inspect the saved unit backup and service logs", flush=True)
            raise
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
