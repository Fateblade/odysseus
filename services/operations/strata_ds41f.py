"""Experimental isolated Strata DS41F readiness, independent from Qwen Strata."""

import hashlib
import json
from pathlib import Path

from services.operations.litellm_config import OperationsError
from services.operations.litellm_host import get_json

MODEL = "deepseek-v4.1-flash"
REVISION = "c62d5e3a15b40ea51c96aae9e8d36928965f53ab"


def inventory(base_url):
    if not base_url:
        return {"configured": False, "status": "disabled", "experimental": True, "message": "Configure the isolated Strata DS41F profile after build and kernel validation."}
    try:
        health = get_json(base_url.rstrip("/") + "/health")
        rows = get_json(base_url.rstrip("/") + "/v1/models").get("data")
        context = health.get("max_context")
        ready = health.get("status") == "ok" and health.get("loaded") is True and health.get("model") == MODEL and type(context) is int and context > 0
        matches = [row for row in rows if isinstance(row, dict) and row.get("id") == MODEL] if isinstance(rows, list) else []
        ready = ready and len(matches) == 1 and matches[0].get("meta", {}).get("n_ctx") == context and matches[0].get("status", {}).get("value") == "loaded"
        return {"configured": True, "experimental": True, "status": "ok" if ready else "unloaded" if health.get("loaded") is False else "degraded",
                "loaded": True if ready else False if health.get("loaded") is False else None, "model": health.get("model"),
                "configured_model": MODEL, "aliases": [MODEL] if ready else [], "max_context": context if ready else None,
                "version": health.get("version"), "message": "Experimental Linux gfx1151 deployment; IST-DAS weights and SSD streaming differ from Maya-XS."}
    except Exception:
        return {"configured": True, "experimental": True, "status": "unavailable", "configured_model": MODEL,
                "message": "Strata DS41F readiness could not be verified. Inspect its isolated service and build validation."}


def qualification(management):
    setup = management.settings.get("strata_ds41f_setup", {})
    try:
        config_path = Path(setup["config_file"]).expanduser()
        receipt_path = Path(setup["build_receipt"]).expanduser()
        if not config_path.is_absolute() or not receipt_path.is_absolute() or config_path.stat().st_size > 1048576 or receipt_path.stat().st_size > 65536:
            raise ValueError()
        config = json.loads(config_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        exe = Path(config["exe"])
        if config.get("model_name") != MODEL or config.get("arch") != "deepseek41" or not exe.is_absolute() or not exe.is_file():
            raise ValueError()
        if receipt.get("source_revision") != REVISION or receipt.get("kernel_checks") != ["ds4_iq_test", "ds4_tq_test", "ds4_tq_test_portable"]:
            raise ValueError()
        with exe.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != receipt.get("binary_sha256"):
            raise ValueError()
        return {"qualified": True, "context": argument(config.get("args", []), "--max-context"),
                "experts": argument(config.get("args", []), "--experts"), "drafter": argument(config.get("args", []), "--dspark")}
    except (KeyError, OSError, ValueError, TypeError):
        raise OperationsError("Strata DS41F requires its isolated config and a matching successful build/kernel-test receipt. Follow STRATA_DS41F_SETUP.md before starting.", 409) from None


def argument(args, key):
    if isinstance(args, list) and key in args and args.index(key) + 1 < len(args):
        return args[args.index(key) + 1]
    return None


def metadata(management):
    setup = management.settings.get("strata_ds41f_setup", {})
    result = {"experimental": True, "recipe": setup.get("recipe"), "expected_source_revision": REVISION}
    try:
        path = Path(setup["config_file"]).expanduser()
        if path.stat().st_size <= 1048576:
            config = json.loads(path.read_text(encoding="utf-8"))
            result.update(experts=argument(config.get("args", []), "--experts"),
                          drafter=argument(config.get("args", []), "--dspark"),
                          prefill_chunk=argument(config.get("args", []), "--prefill-chunk"))
    except (KeyError, OSError, ValueError, TypeError):
        pass
    return result
