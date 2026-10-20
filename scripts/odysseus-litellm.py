#!/usr/bin/env python3
"""Execute one fixed-target LiteLLM operation received as JSON on stdin."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.operations.litellm_config import MAX_CONFIG_BYTES, OperationsError
from services.operations.litellm_host import execute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--container", default="litellm")
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    parser.add_argument("--strata-url", default="http://127.0.0.1:8081")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--physical-memory-gib", type=int, default=0)
    parser.add_argument("--management-config", default="")
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_CONFIG_BYTES + 1)
        if len(raw) > MAX_CONFIG_BYTES:
            raise OperationsError("The operation request exceeds the 1 MiB limit.")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise OperationsError("The operation request must be a JSON object.")
        if payload.get("action") == "overview":
            from services.operations.overview import collect
            result = collect(args.base_url, args.strata_url, args.ollama_url, args.physical_memory_gib)
        elif payload.get("action") in ("management", "residency", "manage", "jobs", "job_detail", "job_start", "job_cancel", "script_save", "strata_config", "strata_preview", "strata_apply"):
            from services.operations.management import Management
            management = Management(args.management_config, args.strata_url, args.ollama_url)
            if payload["action"].startswith("strata_"):
                from services.operations.strata_config import snapshot, review, enqueue_apply
                operation = {"strata_config": snapshot, "strata_preview": review, "strata_apply": enqueue_apply}[payload["action"]]
                result = operation(management) if payload["action"] == "strata_config" else operation(management, payload)
            elif payload["action"] == "management":
                result = management.catalog()
            elif payload["action"] == "residency":
                result = management.residency()
            elif payload["action"] == "manage":
                result = management.execute(payload)
            else:
                from services.operations.jobs import execute as execute_job
                result = execute_job(management, payload)
        else:
            result = execute(args.config, args.container, args.base_url, payload)
        print(json.dumps(result))
    except OperationsError as error:
        print(json.dumps({"error": str(error), "status": error.status}))
    except Exception:
        print(json.dumps({"error": "The host operation failed. Check helper installation and host access.", "status": 502}))


if __name__ == "__main__":
    main()
