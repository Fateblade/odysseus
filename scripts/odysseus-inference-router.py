#!/usr/bin/env python3
"""Run the local lifecycle-aware inference gateway as one host process."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    value = json.loads(Path(args.config).expanduser().read_text(encoding="utf-8"))
    from services.operations.management import Management
    from services.operations.inference_router import Coordinator, create_app
    import uvicorn
    urls = value.get("backends", {})
    management = Management(value["management_config"], urls.get("strata", ""), urls.get("ollama", ""),
                            urls.get("dwarfstar", ""), urls.get("maya", ""), urls.get("maya_glm", ""))
    if not management.path or not management.path.exists():
        raise ValueError("The router requires the existing host management configuration.")
    from urllib.parse import urlsplit
    port = int(value.get("port", 8094))
    if not 1 <= port <= 65535 or any(urlsplit(url).port == port for url in urls.values() if url):
        raise ValueError("Choose a router port different from every native backend.")
    coordinator = Coordinator(management, value["routes"])
    uvicorn.run(create_app(coordinator, reload_routes=lambda: json.loads(Path(args.config).expanduser().read_text(encoding="utf-8"))["routes"]), host="127.0.0.1", port=port, workers=1,
                access_log=False, timeout_graceful_shutdown=3600)


if __name__ == "__main__":
    main()
