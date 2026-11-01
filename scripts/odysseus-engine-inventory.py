#!/usr/bin/env python3
"""Read-only provenance for local inference upgrade trials; never load a model."""

import argparse
import json
import os
import platform
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

KNOBS = {"OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_LOADED_MODELS", "OLLAMA_VULKAN",
         "GGML_VK_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "MAYA_GLM_RESERVED_VRAM", "STRATA_GLM_RESERVED_VRAM",
         "MAYA_GLM_MTP_DECODE", "STRATA_GLM_MTP_DECODE", "MAYA_GLM_MTP_DRAFT", "STRATA_GLM_MTP_DRAFT",
         "DS4_ROCM_Q8_PREQUANT_DECODE", "HSA_OVERRIDE_GFX_VERSION"}


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()[:8192] if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def origin(value):
    if not value:
        return None
    if value.startswith("git@"):
        return value.split("@", 1)[1].split("?", 1)[0]
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https", "ssh") or not parsed.hostname:
        return "local or unrecognized remote (omitted)"
    return urlunsplit((parsed.scheme, parsed.hostname, parsed.path, "", ""))


def source(path):
    path = Path(path).expanduser().resolve()
    return {"origin": origin(command(["git", "-C", str(path), "remote", "get-url", "origin"])),
            "revision": command(["git", "-C", str(path), "rev-parse", "HEAD"]),
            "tag": command(["git", "-C", str(path), "describe", "--tags", "--exact-match"]),
            "changed_files": command(["git", "-C", str(path), "status", "--porcelain"])}


def process(pid):
    path = Path("/proc") / str(pid)
    try:
        executable = os.readlink(path / "exe")
        env = dict(part.split(b"=", 1) for part in (path / "environ").read_bytes().split(b"\0") if b"=" in part)
        return {"pid": pid, "executable": executable, "replaced_binary": executable.endswith(" (deleted)"),
                "settings": {name: env[name.encode()].decode(errors="replace") for name in sorted(KNOBS) if name.encode() in env}}
    except OSError:
        return {"pid": pid, "status": "unavailable"}


def ollama_version(base):
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.username or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Use a loopback HTTP origin for Ollama.")
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(base.rstrip("/") + "/api/version", timeout=5) as response:
            return json.loads(response.read(65536)).get("version")
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--pid", type=int, action="append", default=[], help="Inspect actual running engine PID; only approved tuning variables are included")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = parser.parse_args()
    sources = {}
    for entry in args.source:
        name, separator, path = entry.partition("=")
        if not separator or not name or not path:
            parser.error("Source entries must use NAME=PATH.")
        sources[name] = source(path)
    try:
        version = ollama_version(args.ollama_url)
    except ValueError as error:
        parser.error(str(error))
    memory = {}
    try:
        memory = {key: int(value.split()[0]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                  for key, value in [line.split(":", 1)] if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")}
    except OSError:
        pass
    gpu = []
    for path in Path("/sys/class/drm").glob("card[0-9]*/device"):
        if not (path / "mem_info_vram_total").exists():
            continue
        gpu.append({"device": str(path.resolve()), **{name: int((path / name).read_text()) for name in ("mem_info_vram_total", "mem_info_vram_used")}})
    print(json.dumps({"system": platform.system(), "kernel": platform.release(), "hipcc": command(["hipcc", "--version"]),
                      "ollama_version": version, "sources": sources, "memory_bytes": memory, "gpu": gpu,
                      "processes": [process(pid) for pid in args.pid]}, indent=2))


if __name__ == "__main__":
    main()
