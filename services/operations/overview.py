"""Read-only Linux host telemetry and inference service inventory."""

import json
import os
import platform
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.operations.litellm_host import get_json


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def number(path):
    text = read_text(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def cpu_sample():
    text = read_text("/proc/stat")
    if not text:
        return None
    values = [int(value) for value in text.splitlines()[0].split()[1:9]]
    return sum(values), values[3] + values[4]


def memory():
    text = read_text("/proc/meminfo") or ""
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key] = int(parts[0]) * 1024
    total, available = values.get("MemTotal"), values.get("MemAvailable")
    swap_total, swap_free = values.get("SwapTotal"), values.get("SwapFree")
    return {"total_bytes": total, "available_bytes": available,
            "used_bytes": total - available if total is not None and available is not None else None,
            "swap_total_bytes": swap_total,
            "swap_used_bytes": swap_total - swap_free if swap_total is not None and swap_free is not None else None}


def sensors():
    result = []
    for chip in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        name = read_text(chip / "name") or chip.name
        for sensor in sorted(chip.glob("temp*_input")):
            value = number(sensor)
            if value is None:
                continue
            label = read_text(sensor.with_name(sensor.name.replace("_input", "_label"))) or sensor.stem
            result.append({"chip": name, "label": label, "celsius": value / 1000})
    return result


def gpus():
    result = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        if "-" in card.name or read_text(card / "device/vendor") != "0x1002":
            continue
        device = card / "device"
        result.append({
            "id": card.name, "name": read_text(device / "product_name") or "AMD GPU",
            "utilization_percent": number(device / "gpu_busy_percent"),
            "vram_total_bytes": number(device / "mem_info_vram_total"),
            "vram_used_bytes": number(device / "mem_info_vram_used"),
            "gtt_total_bytes": number(device / "mem_info_gtt_total"),
            "gtt_used_bytes": number(device / "mem_info_gtt_used"),
        })
    return result


def storage():
    try:
        result = subprocess.run(
            ["df", "--local", "--block-size=1", "--output=source,fstype,size,used,avail,pcent,target"],
            capture_output=True, text=True, timeout=5, env={**os.environ, "LC_ALL": "C"})
        items = []
        for line in result.stdout.splitlines()[1:]:
            fields = line.split(maxsplit=6)
            if len(fields) != 7 or fields[1] in ("tmpfs", "devtmpfs", "ramfs", "rootfs", "squashfs", "overlay", "efivarfs"):
                continue
            source, filesystem, total, used, available, percent, mount = fields
            try:
                items.append({"source": source, "filesystem": filesystem, "mount": mount,
                              "total_bytes": int(total), "used_bytes": int(used), "available_bytes": int(available),
                              "used_percent": int(percent.rstrip("%"))})
            except ValueError:
                continue
        if not items:
            raise ValueError()
        return {"status": "ok" if result.returncode == 0 else "degraded", "items": items,
                "message": "Local disk filesystems only. Available space excludes filesystem-reserved blocks."}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "items": [], "message": "Disk space could not be sampled on the host."}


def host_metrics(physical_memory_gib=0):
    from src.host_docker_access import running_in_container
    if platform.system() != "Linux":
        return {"status": "unavailable", "message": "Linux host telemetry requires a helper on the Linux machine."}
    if running_in_container():
        return {"status": "unavailable", "message": "This helper is inside a container. Connect the host helper to read host telemetry."}
    first = cpu_sample()
    time.sleep(0.2)
    second = cpu_sample()
    utilization = None
    if first and second and second[0] > first[0]:
        utilization = round(100 * (1 - (second[1] - first[1]) / (second[0] - first[0])), 1)
    cpu_info = read_text("/proc/cpuinfo") or ""
    cpu_name = next((line.split(":", 1)[1].strip() for line in cpu_info.splitlines() if line.startswith("model name")), "CPU")
    return {"status": "ok", "hostname": platform.node(), "kernel": platform.release(),
            "cpu": {"name": cpu_name, "threads": os.cpu_count(), "utilization_percent": utilization},
            "physical_memory_bytes": physical_memory_gib * 1024 ** 3 if physical_memory_gib else None,
            "memory": memory(), "gpus": gpus(), "sensors": sensors()}


def containers():
    from src.host_docker_access import running_in_container, host_docker_access_enabled
    if running_in_container() and not host_docker_access_enabled():
        return {"status": "unavailable", "message": "Host Docker access is not configured.", "items": []}
    try:
        result = subprocess.run(["docker", "ps", "-a", "--format", "{{json .}}"],
                                capture_output=True, timeout=5, text=True)
        if result.returncode:
            raise OSError()
        items = []
        for line in result.stdout.splitlines():
            row = json.loads(line)
            items.append({"name": row.get("Names"), "image": row.get("Image"),
                          "state": row.get("State"), "status": row.get("Status"), "ports": row.get("Ports")})
        return {"status": "ok", "items": items}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "message": "Container inventory is unavailable. Check the helper's Docker access.", "items": []}


def strata(base_url):
    try:
        health = get_json(base_url.rstrip("/") + "/health")
    except Exception:
        return {"status": "unavailable", "message": "Strata is unreachable or requires authentication."}
    result = {"status": "ok" if health.get("status") == "ok" else "degraded",
              "model": health.get("model"), "loaded": health.get("loaded"), "max_context": health.get("max_context")}
    try:
        metrics = get_json(base_url.rstrip("/") + "/metrics")
        engine, live = metrics.get("engine", {}), metrics.get("live", {})
        result.update({"version": engine.get("version"), "state": live.get("state"),
                       "queued": live.get("queued"), "generation_tps": live.get("tok_s_mean"),
                       "prefill_tps": live.get("prefill_tok_s_mean")})
        live_keys = ("state", "phase", "queued", "running", "waiting", "admitting", "prompt_tokens",
                     "prompt_read", "prompt_total", "generated", "max_tokens", "elapsed_s", "tok_s", "tok_s_mean")
        result["activity"] = {key: live.get(key) for key in live_keys}
        result["recent_requests"] = [{key: row.get(key) for key in
            ("time", "duration_s", "finish", "prompt_tokens", "output_tokens", "decode_tok_s")}
            for row in metrics.get("requests", [])[:12] if isinstance(row, dict)]
        result["metrics_available"] = True
    except Exception:
        result["metrics_available"] = False
    try:
        records = get_json(base_url.rstrip("/") + "/api/requests")
        if not isinstance(records, dict) or not isinstance(records.get("requests"), list):
            raise ValueError()
        result["requests"] = [{key: row.get(key) for key in
            ("id", "path", "model", "started_at", "state", "queue_s", "load_s", "first_token_s", "wallclock_s")}
            for row in records.get("requests", [])[:30] if isinstance(row, dict)]
        result["requests_available"] = True
    except Exception:
        result["requests_available"] = False
    return result


def ollama(base_url):
    result = {"status": "ok", "installed": [], "loaded": [], "installed_available": False, "loaded_available": False}
    for path, key in (("/api/tags", "installed"), ("/api/ps", "loaded")):
        try:
            data = get_json(base_url.rstrip("/") + path)
            result[key] = [{"name": row.get("name") or row.get("model"), "digest": row.get("digest"),
                            "size_bytes": row.get("size"), "gpu_bytes": row.get("size_vram"),
                            "context_length": row.get("context_length"), "expires_at": row.get("expires_at")}
                           for row in data.get("models", [])]
            result[key + "_available"] = True
        except Exception:
            result["message"] = "Some Ollama inventory could not be read. Check service access."
    result["status"] = "ok" if result["installed_available"] and result["loaded_available"] else "degraded" if result["installed_available"] or result["loaded_available"] else "unavailable"
    return result


def litellm(base_url):
    try:
        get_json(base_url.rstrip("/") + "/health/liveliness")
        return {"status": "ok"}
    except Exception:
        return {"status": "unavailable", "message": "LiteLLM is not responding to its liveness probe."}


def collect(base_url, strata_url, ollama_url, physical_memory_gib=0, config_path="", container="litellm", dwarfstar_url=""):
    from services.operations.activity import processes, gateway_activity
    from services.operations.dwarfstar import inventory
    from src.host_docker_access import running_in_container
    if platform.system() != "Linux" or running_in_container():
        return {"configured": False, "message": "Connect the helper on the Linux host. Container and Windows readings are not substituted for host telemetry."}
    jobs = {"host": lambda: host_metrics(physical_memory_gib), "containers": containers,
            "strata": lambda: strata(strata_url), "ollama": lambda: ollama(ollama_url),
            "dwarfstar": lambda: inventory(dwarfstar_url),
            "litellm": lambda: litellm(base_url), "processes": processes,
            "gateway_activity": lambda: gateway_activity(base_url, config_path, container), "storage": storage}
    result = {"configured": True, "sampled_at": time.time()}
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {name: executor.submit(job) for name, job in jobs.items()}
        for name, future in futures.items():
            try:
                result[name] = future.result()
            except Exception:
                result[name] = {"status": "unavailable", "message": "This source could not be sampled."}
    return result
