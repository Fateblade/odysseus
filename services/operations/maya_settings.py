"""Reviewed Maya run-config edits with service restart and recovery."""

import hashlib
import json
import re
import shlex
import signal
import subprocess
import time
from pathlib import Path

from services.operations.dwarfstar_config import replace_unit
from services.operations.jobs import write_json
from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import configuration_lock, get_json
from services.operations.maya import inventory, run_service


def config_path(management):
    return Path(management.settings.get("maya_setup", {}).get(
        "config_file", Path.home() / "project-maya-xs/maya-ds4-hip.json")).expanduser()


def enabled(management):
    return bool(management.settings.get("maya_setup", {}).get("config_file")) or config_path(management).is_file()


def argument(args, name):
    indices = [i for i, arg in enumerate(args) if arg == name or arg.startswith(name + "=")]
    if len(indices) != 1 or args[indices[0]] != name or indices[0] + 1 >= len(args):
        raise OperationsError(f"Maya config needs exactly one separate {name} argument.", 503)
    return indices[0] + 1


def systemctl(*args):
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, timeout=180)
    if result.returncode:
        raise OperationsError("Maya service operation failed. Inspect its user-service logs.", 502)
    return result.stdout.decode(errors="replace")


def profile(management):
    setup = management.settings.get("maya_setup", {})
    setting = management.settings.get("services", {}).get("maya", {})
    setting = {"unit": setting} if isinstance(setting, str) else setting
    unit = setting.get("unit", "")
    if (not management.maya_url or not re.fullmatch(r"[\w][\w.@-]*\.service", unit)
            or setting.get("scope", "user") != "user" or setting.get("sudo")):
        raise OperationsError("Maya settings require an allowlisted Maya user service without sudo.", 503)
    path = config_path(management)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise OperationsError("Maya config_file must be an existing absolute file without a symlink.", 503)
    raw = path.read_bytes()
    if len(raw) > 1048576:
        raise OperationsError("Maya run config exceeds 1 MiB.", 503)
    try:
        config = json.loads(raw)
        args = config["args"]
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise ValueError()
        ctx_index, draft_index = argument(args, "--max-context"), argument(args, "--dspark")
        pack = Path(args[argument(args, "--pack")]).expanduser()
        context = int(args[ctx_index])
        if context < 4096 or args[draft_index] not in ("none", "off", "on") or not pack.is_absolute():
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise OperationsError("Maya run config must contain valid context, DSpark and absolute pack arguments.", 503) from None
    if not management.path or management.path.is_symlink():
        raise OperationsError("Maya settings require a regular host management JSON file.", 503)
    settings_raw = management.path.read_bytes()
    if json.loads(settings_raw) != management.settings:
        raise OperationsError("Host management settings changed. Reload before reviewing.", 409)
    maximum = setup.get("maximum_context", 4096)
    if type(maximum) is not int or not 4096 <= maximum <= 1048576:
        raise OperationsError("Maya maximum_context must be between 4096 and 1048576 tokens.", 503)
    data = {"url": management.maya_url, "path": str(path)}
    revision = hashlib.sha256(json.dumps(data, sort_keys=True).encode() + b"\0" + raw + b"\0" + settings_raw).hexdigest()
    return {"path": path, "raw": raw, "config": config, "context": context,
            "drafting": args[draft_index] == "on", "draft_mode": args[draft_index], "pack": pack,
            "ctx_index": ctx_index, "draft_index": draft_index, "unit": unit,
            "unit_path": Path(setup.get("unit_file", Path.home() / ".config/systemd/user" / unit)).expanduser(),
            "revision": revision, "settings_raw": settings_raw}


def service_state(value):
    output = systemctl("show", value["unit"], "--all", "--property=FragmentPath,DropInPaths,ActiveState,NeedDaemonReload")
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    path = value["unit_path"]
    if (not path.is_absolute() or path.is_symlink() or fields.get("FragmentPath") != str(path)
            or fields.get("DropInPaths") != "" or fields.get("NeedDaemonReload") != "no"):
        raise OperationsError("Maya must use its configured user unit without drop-ins or a pending daemon reload.", 409)
    text = path.read_text()
    starts = re.findall(r"(?m)^ExecStart=(.+)$", text)
    try:
        args = shlex.split(starts[0].replace("%h", str(Path.home()))) if len(starts) == 1 else []
        index = argument(args, "--config")
        if "serve.server" not in args or Path(args[index]) != value["path"] or "\\" in starts[0] or "$" in starts[0]:
            raise ValueError()
    except (OperationsError, ValueError):
        raise OperationsError("Maya unit must directly launch serve.server with this run config.", 503) from None
    if fields.get("ActiveState") not in ("active", "inactive", "failed"):
        raise OperationsError("Maya service is changing state. Wait before applying settings.", 409)
    return fields["ActiveState"] == "active"


def has_drafter(value):
    try:
        with (value["pack"] / "tensors.txt").open() as file:
            return any(line.startswith("t mtp.") for line in file)
    except OSError:
        return False


def snapshot(management):
    try:
        value = profile(management)
        running = service_state(value)
        live = inventory(management.maya_url)
        minimum, maximum = 4096, 262144
        if running:
            limits = get_json(management.maya_url.rstrip("/") + "/api/context")
            minimum, maximum = limits["min"], limits["max"]
            if (any(type(v) is not int for v in (minimum, maximum)) or not 4096 <= minimum <= value["context"] <= maximum
                    or limits.get("context") != value["context"]
                    or live.get("status") != "ok" or live.get("max_context") != value["context"]
                    or (limits.get("reload") or {}).get("state") in ("waiting", "running")):
                raise OperationsError("Maya must be ready with its saved context before editing settings.", 409)
        elif live.get("status") != "unavailable":
            raise OperationsError("A Maya server answers outside the managed active service. Stop it first.", 409)
        identity = {"running": running, "model": live.get("model"), "version": live.get("version"),
                    "minimum": minimum, "maximum": maximum}
        revision = hashlib.sha256(value["revision"].encode() + json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return {"configured": True, "managed_settings": True, "revision": revision, "config_revision": value["revision"],
                "model": live.get("model"), "unit": value["unit"], "running": running,
                "configured_context": value["context"], "active_context": live.get("max_context"),
                "minimum_context": minimum, "maximum_context": maximum,
                "drafting": value["drafting"], "draft_mode": value["draft_mode"], "drafter_available": has_drafter(value)}
    except (OperationsError, OSError, ValueError, KeyError, TypeError):
        return {"configured": False, "message": "Maya settings are unavailable. Verify maya_setup.config_file, the user service and its saved/live context; inspect host logs."}


def review(management, payload):
    current = snapshot(management)
    if not current["configured"]:
        raise OperationsError(current["message"], 503)
    if payload.get("revision") != current["revision"]:
        raise OperationsError("Maya state changed. Reload and review again.", 409)
    context, drafting = payload.get("context"), payload.get("drafting")
    if type(context) is not int or not current["minimum_context"] <= context <= current["maximum_context"]:
        raise OperationsError(f"Context must be between {current['minimum_context']} and {current['maximum_context']} tokens.")
    if type(drafting) is not bool:
        raise OperationsError("Choose whether DSpark speculative decoding is enabled.")
    if drafting and not current["drafter_available"]:
        raise OperationsError("The selected Maya pack has no MTP drafter. Verify the matching XS drafter before enabling DSpark.")
    digest = hashlib.sha256(f"{current['revision']}:{context}:{drafting}".encode()).hexdigest()
    return {**current, "context": context, "drafting": drafting, "review_digest": digest,
            "display_change": f"Maya context: {current['configured_context']} → {context} tokens\nDSpark: {current['draft_mode']} → {'on' if drafting else 'none'}\nRunning service restarts; stopped service stays stopped. More context and drafting use more memory."}


def await_settings(management, context, model, drafting):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        live = inventory(management.maya_url)
        if live.get("status") == "ok" and live.get("max_context") == context and (not model or live.get("model") == model):
            try:
                maps = get_json(management.maya_url.rstrip("/") + "/metrics").get("gpu_map")
                if (isinstance(maps, list) and maps and all(type(row.get("drafter")) is int for row in maps)
                        and any(row["drafter"] > 0 for row in maps) == drafting):
                    return
            except (OperationsError, OSError, ValueError, AttributeError):
                pass
        time.sleep(2)
    raise OperationsError("Maya did not report the requested model, context and drafter residency after startup.", 502)


def run(management, specification, folder):
    value = profile(management)
    with configuration_lock(management.path), configuration_lock(value["path"]):
        reviewed = review(management, specification)
        if reviewed["review_digest"] != specification.get("review_digest"):
            raise OperationsError("The reviewed Maya settings no longer match.", 409)
        value = profile(management)
        if value["revision"] != reviewed["config_revision"]:
            raise OperationsError("Maya configuration changed before the job started.", 409)
        running = service_state(value)
        config = value["config"]
        config["args"][value["ctx_index"]] = str(reviewed["context"])
        config["args"][value["draft_index"]] = "on" if reviewed["drafting"] else "none"
        replacement = (json.dumps(config, indent=2) + "\n").encode()
        settings = json.loads(value["settings_raw"])
        setup = settings.setdefault("maya_setup", {})
        setup["maximum_context"] = max(setup.get("maximum_context", 4096), reviewed["context"])
        settings_replacement = (json.dumps(settings, indent=2) + "\n").encode()
        files = [(value["path"], value["raw"], replacement), (management.path, value["settings_raw"], settings_replacement)]
        backup = folder / "maya-backup"
        backup.mkdir(mode=0o700)
        for index, (_, raw, _) in enumerate(files):
            replace_unit(backup / f"{index}.json", raw, 0o600)
        write_json(backup / "manifest.json", {"files": [str(path) for path, _, _ in files], "running": running})
        written, stopped = [], False

        def cancelled(*_):
            raise InterruptedError("Maya settings change cancelled")

        previous_handler = signal.signal(signal.SIGTERM, cancelled)
        try:
            if running:
                stopped = True
                management.service("maya", "stop")
            if inventory(management.maya_url).get("status") != "unavailable":
                raise OperationsError("Another Maya server still answers after the service stopped.", 409)
            service_state(value)
            for path, raw, new in files:
                if path.is_symlink() or path.read_bytes() != raw:
                    raise OperationsError("Maya configuration changed during the operation.", 409)
                replace_unit(path, new, path.stat().st_mode & 0o777)
                written.append(path)
            print(reviewed["display_change"], flush=True)
            if running:
                run_service(management, {"operation": "start"})
                await_settings(management, reviewed["context"], reviewed["model"], reviewed["drafting"])
            return {"configured_context": reviewed["context"], "drafting": reviewed["drafting"],
                    "running": running, "backup": str(backup)}
        except Exception:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            restored = False
            try:
                for path, raw, new in files:
                    if path.is_symlink() or path.read_bytes() != (new if path in written else raw):
                        raise OperationsError("Configuration was edited externally; recovery will not overwrite it.", 409)
                if stopped or written:
                    management.service("maya", "stop")
                    for path, raw, _ in files:
                        replace_unit(path, raw, path.stat().st_mode & 0o777)
                    if running:
                        run_service(management, {"operation": "start"})
                        await_settings(management, value["context"], reviewed["model"], value["draft_mode"] != "none")
                restored = True
            finally:
                write_json(folder / "result.json", {"restored": restored, "backup": str(backup)})
                print("Previous Maya configuration and service state restored" if restored else "Recovery unverified; inspect the saved backup and service logs", flush=True)
            raise
        finally:
            signal.signal(signal.SIGTERM, previous_handler)
