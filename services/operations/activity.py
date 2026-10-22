"""Bounded, read-only Linux process samples and gateway activity."""

import os
import time
from pathlib import Path

from services.operations.overview import read_text


def gpu_clients(process):
    clients = {}
    try:
        for fd in (process / "fd").iterdir():
            try:
                if not os.readlink(fd).startswith("/dev/dri/"):
                    continue
                fields = {}
                for line in (read_text(process / "fdinfo" / fd.name) or "").splitlines():
                    key, _, value = line.partition(":")
                    fields[key] = value.strip()
                client = fields.get("drm-client-id")
                if not client:
                    continue
                key = fields.get("drm-pdev", fields.get("drm-driver", "")) + ":" + client
                engines, allocated = {}, None
                for name, value in fields.items():
                    parts = value.split()
                    if len(parts) != 2 or not parts[0].isdigit():
                        continue
                    if name.startswith("drm-engine-") and parts[1] == "ns":
                        engines[name] = int(parts[0])
                    if name == "drm-memory-vram" and parts[1] in ("KiB", "MiB", "bytes"):
                        allocated = int(parts[0]) * {"KiB": 1024, "MiB": 1024 ** 2, "bytes": 1}[parts[1]]
                clients[key] = {"engines": engines, "vram_bytes": allocated}
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return clients


def process_snapshot(proc_root=Path("/proc")):
    result = {}
    skipped = 0
    for process in proc_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            raw = (process / "stat").read_text(errors="replace")
            name = raw[raw.index("(") + 1:raw.rindex(")")]
            fields = raw.rsplit(")", 1)[1].split()
            if fields[0] == "Z":
                continue
            units = [part for line in (read_text(process / "cgroup") or "").splitlines()
                     for part in line.split("/") if part.endswith((".service", ".scope"))]
            result[int(process.name)] = {
                "pid": int(process.name), "name": name[:120], "state": fields[0],
                "cpu_ticks": int(fields[11]) + int(fields[12]), "identity": fields[19],
                "rss_pages": max(0, int(fields[21])), "threads": int(fields[17]),
                "unit": units[-1][:160] if units else None, "gpu_clients": gpu_clients(process),
            }
        except (OSError, ValueError, IndexError):
            skipped += 1
    return result, skipped


def process_usage(first, second, elapsed, ticks, page_size):
    rows = []
    for pid, current in second.items():
        before = first.get(pid)
        stable = before is not None and before["identity"] == current["identity"]
        cpu = max(0, current["cpu_ticks"] - before["cpu_ticks"]) / ticks / elapsed * 100 if stable else None
        gpu_ns, measured = 0, False
        for client, counters in current["gpu_clients"].items():
            previous = before["gpu_clients"].get(client) if stable else None
            if not previous:
                continue
            for engine, value in counters["engines"].items():
                if engine in previous["engines"]:
                    measured = True
                    gpu_ns += max(0, value - previous["engines"][engine])
        allocations = [c["vram_bytes"] for c in current["gpu_clients"].values() if c["vram_bytes"] is not None]
        rows.append({"pid": pid, "name": current["name"], "state": current["state"], "unit": current["unit"],
                     "threads": current["threads"], "cpu_percent": round(cpu, 1) if cpu is not None else None,
                     "rss_bytes": current["rss_pages"] * page_size,
                     "gpu_engine_percent": round(gpu_ns / elapsed / 1e9 * 100, 1) if measured else None,
                     "gpu_vram_bytes": sum(allocations) if allocations else None})
    selected = {}
    for key, count in (("cpu_percent", 25), ("rss_bytes", 10), ("gpu_engine_percent", 10), ("gpu_vram_bytes", 10)):
        for row in sorted(rows, key=lambda row: row[key] or 0, reverse=True)[:count]:
            selected[row["pid"]] = row
    return sorted(selected.values(), key=lambda row: row["cpu_percent"] or 0, reverse=True)


def processes():
    try:
        started = time.monotonic()
        first, _ = process_snapshot()
        time.sleep(0.3)
        second, skipped = process_snapshot()
        elapsed = time.monotonic() - started
        items = process_usage(first, second, elapsed, os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE"))
        return {"status": "ok", "items": items, "sample_seconds": round(elapsed, 2),
                "visible_processes": len(second), "skipped": skipped,
                "gpu_available": any(row["gpu_engine_percent"] is not None for row in items)}
    except (OSError, ValueError):
        return {"status": "unavailable", "items": [], "message": "Host process sampling is unavailable. Check /proc access."}


def gateway_activity(base_url, config_path, container):
    from services.operations.litellm_config import load_config
    from services.operations.litellm_host import get_json, resolve_secret

    try:
        key = ""
        if config_path:
            config = load_config(Path(config_path).expanduser().read_bytes())
            key = resolve_secret(config.get("general_settings", {}).get("master_key", ""), container)
        result = get_json(base_url.rstrip("/") + "/health/backlog", key)
        count = result.get("in_flight_requests")
        if type(count) is not int or count < 0:
            raise ValueError()
        return {"status": "ok", "in_flight_requests": count}
    except Exception:
        return {"status": "unavailable", "message": "Gateway in-flight count unavailable; this LiteLLM version may not expose /health/backlog or access may be restricted."}
