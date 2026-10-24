"""Durable host jobs and saved Python script definitions, independent of agent jobs."""

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import configuration_lock


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as file:
            json.dump(value, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def root(management):
    value = management.settings.get("jobs_directory")
    if not value:
        raise OperationsError("Configure jobs_directory in the host management file first.", 503)
    directory = Path(value).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def script_definition(management, value):
    base = management.settings.get("scripts_root")
    if not base:
        raise OperationsError("Configure scripts_root on the host first.", 503)
    path = (Path(base).resolve() / str(value.get("path", ""))).resolve()
    if not path.is_relative_to(Path(base).resolve()) or path.suffix != ".py" or not path.is_file():
        raise OperationsError("Select an existing Python file inside scripts_root.")
    args = value.get("arguments", [])
    if not isinstance(args, list) or len(args) > 64 or any(not isinstance(a, str) or len(a) > 4096 or "\x00" in a for a in args):
        raise OperationsError("Arguments must be a JSON array of up to 64 strings.")
    timeout = value.get("timeout", 600)
    if type(timeout) != int or not 1 <= timeout <= 86400:
        raise OperationsError("Script timeout must be between 1 and 86400 seconds.")
    name = str(value.get("name", "")).strip()
    if not 1 <= len(name) <= 120:
        raise OperationsError("Supply a script name (at most 120 characters).")
    return {"name": name, "path": str(path.relative_to(Path(base).resolve())), "arguments": args, "timeout": timeout}


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + fields[19]
    except (OSError, IndexError):
        return None


def read_job(directory):
    value = json.loads((directory / "job.json").read_text())
    if value["state"] in ("queued", "running") and time.time() - value["created_at"] > 10:
        owner = value.get("worker_pid")
        if not owner or identity(owner) != value.get("worker_identity"):
            value = {**value, "state": "interrupted", "error": "Worker stopped; this job was not resumed."}
    return value


def job_directory(management, value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise OperationsError("Invalid job identifier.")
    directory = root(management) / value
    if not (directory / "job.json").exists():
        raise OperationsError("Job not found.", 404)
    return directory


def enqueue(management, specification):
    if sys.platform != "linux":
        raise OperationsError("Jobs must execute through the Linux host helper.", 503)
    directory = root(management)
    with configuration_lock(directory / "queue"):
        active = [read_job(p) for p in directory.iterdir() if p.is_dir() and (p / "job.json").exists()]
        if any(j["state"] in ("queued", "running") for j in active):
            raise OperationsError("A host job is already active. Wait or cancel it before starting another.", 409)
        job_id = uuid.uuid4().hex
        folder = directory / job_id
        folder.mkdir(mode=0o700)
        record = {"id": job_id, "state": "queued", "created_at": time.time(), "specification": specification,
                  "audit": getattr(management, "audit_actor", {})}
        write_json(folder / "job.json", record)
        worker = Path(__file__).resolve().parents[2] / "scripts" / "odysseus-operations-worker.py"
        try:
            with (folder / "worker.log").open("wb") as log:
                subprocess.Popen([sys.executable, str(worker), str(folder), str(management.path or ""), management.strata_url, management.ollama_url,
                                  "--dwarfstar-url", management.dwarfstar_url, "--maya-url", management.maya_url],
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, close_fds=True)
        except OSError:
            write_json(folder / "job.json", {**record, "state": "failed", "error": "Could not start host worker."})
            raise OperationsError("Could not start host worker.", 502) from None
    return {"status": "queued", "job": record}


def execute(management, payload):
    if payload["action"] in ("measurements", "measurement_import"):
        from services.operations.scoreboard import execute as measurements
        return measurements(management, payload)
    directory = root(management)
    definitions = directory / "scripts.json"
    action = payload["action"]
    if action == "jobs":
        jobs = [read_job(p) for p in directory.iterdir() if p.is_dir() and (p / "job.json").exists()]
        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        public = []
        for job in jobs[:100]:
            item = {**job, "specification": {k: v for k, v in job["specification"].items() if k not in ("prompt", "tasks", "warmup_task")}}
            if "result" in item:
                item["result"] = {"summary": item["result"].get("summary", {})} if isinstance(item["result"], dict) else {}
            public.append(item)
        from services.operations.benchmark_tasks import catalog
        return {"benchmark": catalog(), "jobs": public, "scripts": json.loads(definitions.read_text()) if definitions.exists() else [], "scripts_enabled": bool(management.settings.get("scripts_root"))}
    if action == "job_detail":
        folder = job_directory(management, payload.get("id"))
        log_path = folder / "output.log"
        with log_path.open("rb") if log_path.exists() else open(os.devnull, "rb") as file:
            file.seek(0, 2)
            file.seek(max(0, file.tell() - 65536))
            log = file.read().decode(errors="replace")
        return {"job": read_job(folder), "log": log}
    if action == "job_cancel":
        folder = job_directory(management, payload.get("id"))
        if read_job(folder)["state"] not in ("queued", "running"):
            raise OperationsError("This job is already finished.", 409)
        (folder / "cancel").touch(mode=0o600)
        return {"status": "cancellation requested"}
    if action == "script_save":
        with configuration_lock(definitions):
            scripts = json.loads(definitions.read_text()) if definitions.exists() else []
            if payload.get("remove"):
                scripts = [s for s in scripts if s["name"] != payload.get("name")]
            else:
                definition = script_definition(management, payload)
                scripts = [s for s in scripts if s["name"] != definition["name"]] + [definition]
            write_json(definitions, scripts)
        return {"status": "saved"}
    if action == "job_start":
        if payload.get("kind") == "script":
            scripts = json.loads(definitions.read_text()) if definitions.exists() else []
            definition = next((s for s in scripts if s["name"] == payload.get("name")), None)
            if not definition:
                raise OperationsError("Saved script not found.", 404)
            return enqueue(management, {"kind": "script", **script_definition(management, definition)})
        if payload.get("kind") == "benchmark":
            from services.operations.management import model_name
            backend = payload.get("backend")
            if backend not in ("ollama", "strata", "dwarfstar", "maya"):
                raise OperationsError("Choose Ollama, Strata, DwarfStar or Maya.")
            if backend == "dwarfstar" and not management.dwarfstar_url:
                raise OperationsError("Configure the DwarfStar endpoint on the host helper first.", 503)
            if backend == "maya" and not management.maya_url:
                raise OperationsError("Configure the Maya endpoint on the host helper first.", 503)
            from services.operations.benchmark_tasks import plan, MAX_RUNS, MAX_OUTPUT_TOKENS
            runs, tokens = payload.get("runs", 3), payload.get("max_tokens", 256)
            if type(runs) != int or not 1 <= runs <= MAX_RUNS or type(tokens) != int or not 1 <= tokens <= MAX_OUTPUT_TOKENS:
                raise OperationsError(f"Use 1–{MAX_RUNS} runs and 1–{MAX_OUTPUT_TOKENS} output tokens.")
            timeout = payload.get("timeout", 7200)
            if type(timeout) is not int or not 60 <= timeout <= 604800:
                raise OperationsError("Benchmark timeout must be 60–604800 seconds.")
            workload = plan(payload, runs)
            from services.operations.scoreboard import declared_settings
            declared = declared_settings(payload)
            notes = payload.get("notes", "")
            if not isinstance(notes, str) or len(notes) > 2000:
                raise OperationsError("Notes must be at most 2000 characters.")
            from services.operations.benchmark_limits import limits, validate_budget
            budget = limits(management, payload, workload)
            validate_budget(budget, tokens)
            return enqueue(management, {"kind": "benchmark", "backend": backend, "model": model_name(payload.get("model")),
                                       **workload, "context_budget": budget, "runs": runs, "max_tokens": tokens, "warmup": bool(payload.get("warmup", True)),
                                       "declared": declared, "notes": notes, "timeout": timeout})
    raise OperationsError("Unknown job operation.")
