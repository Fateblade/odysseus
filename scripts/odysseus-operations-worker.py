#!/usr/bin/env python3
"""Detached supervisor: persists results, bounds logs, and owns cancellation."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.operations.jobs import identity, write_json
from services.operations.management import Management, api


def benchmark(management, specification):
    from services.operations.benchmark import run
    return run(management, specification)


def perform(folder, management, specification):
    kind = specification["kind"]
    if kind == "benchmark":
        result = benchmark(management, specification)
    elif kind == "strata_setup":
        from services.operations.strata_config import run
        result = run(management, specification, folder)
    elif kind == "strata":
        result = api(management.strata_url, "/" + specification["operation"], {}, timeout=290)
        if result.get("status") in ("busy", "unsupported"):
            raise RuntimeError("Strata cannot change model residency while busy or unsupported.")
    elif kind == "residency":
        result = api(management.ollama_url, "/api/generate", {"model": specification["model"], "stream": False,
                     "keep_alive": 0 if specification["operation"] == "unload" else "10m"}, timeout=290)
    elif kind == "pull":
        from urllib.request import Request, build_opener
        from services.operations.litellm_host import NoRedirect
        request = Request(management.ollama_url.rstrip("/") + "/api/pull",
                          data=json.dumps({"model": specification["model"], "stream": True}).encode(),
                          headers={"Content-Type": "application/json"})
        result = None
        with build_opener(NoRedirect()).open(request, timeout=120) as response:
            for raw in response:
                result = json.loads(raw)
                if result.get("error"):
                    raise RuntimeError("Ollama model download failed.")
                print(json.dumps(result), flush=True)
        if not result or result.get("status") != "success":
            raise RuntimeError("Ollama did not confirm a successful download.")
    else:
        raise RuntimeError("Unknown worker operation.")
    write_json(folder / "result.json", result)


def supervise(folder, management):
    record = json.loads((folder / "job.json").read_text())
    specification = record["specification"]
    record.update(state="running", started_at=time.time(), worker_pid=os.getpid(), worker_identity=identity(os.getpid()))
    write_json(folder / "job.json", record)
    environment = dict(os.environ, ODYSSEUS_JOB_RESULT_PATH=str(folder / "result.json"))
    if specification["kind"] == "script":
        from services.operations.jobs import script_definition
        specification = script_definition(management, specification)
        path = Path(management.settings["scripts_root"]).resolve() / specification["path"]
        interpreter = management.settings.get("python", sys.executable)
        if not Path(interpreter).is_absolute():
            raise RuntimeError("Configure an absolute Python interpreter path on the host.")
        command = [interpreter, "-u", str(path), *specification["arguments"]]
        cwd = path.parent
    else:
        command = [sys.executable, __file__, str(folder), str(management.path or ""), management.strata_url, management.ollama_url, "--execute"]
        cwd = folder
    child = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    def drain():
        written = 0
        with (folder / "output.log").open("wb") as output:
            while data := child.stdout.read1(8192):
                if written < 4 * 1024 * 1024:
                    kept = data[:4 * 1024 * 1024 - written]
                    output.write(kept)
                    output.flush()
                    written += len(kept)
            if written >= 4 * 1024 * 1024:
                output.write(b"\n[Log truncated at 4 MiB]\n")
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    state = "completed"
    deadline = time.monotonic() + specification["timeout"]
    try:
        while child.poll() is None:
            if (folder / "cancel").exists() or time.monotonic() >= deadline:
                state = "cancelled" if (folder / "cancel").exists() else "timed_out"
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=540 if specification.get("kind") == "strata_setup" else 5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                break
            time.sleep(0.2)
        child.wait()
    finally:
        # Terminate any script descendants still in the group, even after normal exit.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        reader.join(timeout=5)
    record.update(state="failed" if state == "completed" and child.returncode else state,
                  finished_at=time.time(), exit_code=child.returncode)
    result_path = folder / "result.json"
    if result_path.exists():
        if result_path.stat().st_size > 262144:
            record["result_error"] = "Result exceeds the 256 KiB limit."
        else:
            try:
                record["result"] = json.loads(result_path.read_text())
            except (ValueError, OSError):
                record["result_error"] = "Result file is not valid JSON."
    write_json(folder / "job.json", record)


if __name__ == "__main__":
    folder = Path(sys.argv[1]).resolve()
    management = Management(sys.argv[2], sys.argv[3], sys.argv[4])
    try:
        if "--execute" in sys.argv:
            perform(folder, management, json.loads((folder / "job.json").read_text())["specification"])
        else:
            supervise(folder, management)
    except Exception as error:
        print(str(error), flush=True)
        if "--execute" not in sys.argv:
            record = json.loads((folder / "job.json").read_text())
            write_json(folder / "job.json", {**record, "state": "failed", "finished_at": time.time(), "error": "Worker failed; inspect its host log."})
        sys.exit(1)
