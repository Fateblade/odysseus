"""Credential-free editing views over round-trip LiteLLM YAML."""

import copy
import difflib
import hashlib
import io
import json
from urllib.parse import urlsplit

from ruamel.yaml import YAML

MAX_CONFIG_BYTES = 1024 * 1024
UNCHANGED = "[unchanged protected value]"
STRATEGIES = (
    "simple-shuffle", "least-busy", "usage-based-routing",
    "latency-based-routing", "cost-based-routing", "usage-based-routing-v2",
)


class OperationsError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def revision(raw):
    return hashlib.sha256(raw).hexdigest()


def yaml_codec():
    codec = YAML(typ="rt")
    codec.preserve_quotes = True
    codec.allow_duplicate_keys = False
    return codec


def load_config(raw):
    if len(raw) > MAX_CONFIG_BYTES:
        raise OperationsError("LiteLLM configuration exceeds the 1 MiB limit.")
    try:
        config = yaml_codec().load(raw.decode("utf-8-sig"))
        if not isinstance(config, dict) or not isinstance(config.get("model_list"), list):
            raise ValueError()
        if not all(isinstance(row, dict) and isinstance(row.get("litellm_params"), dict)
                   for row in config["model_list"]):
            raise ValueError()
        if not isinstance(config.get("router_settings", {}), dict):
            raise ValueError()
    except Exception:
        raise OperationsError("LiteLLM YAML must contain a valid model_list and router_settings.") from None
    return config


def public_url(value):
    if not value:
        return ""
    if not isinstance(value, str):
        return UNCHANGED
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return UNCHANGED
        parsed.port
        return value
    except ValueError:
        return UNCHANGED


def identifier(value, label):
    if (not isinstance(value, str) or not value.strip() or len(value) > 512
            or any(ord(char) < 32 for char in value)):
        raise OperationsError(f"{label} must be a non-empty name without control characters.")
    return value.strip()


def drop_parameters(value):
    if value is None:
        return []
    if (not isinstance(value, list) or len(value) > 100
            or any(not isinstance(name, str) or not name or len(name) > 512
                   or any(char.isspace() or ord(char) < 32 for char in name) for name in value)):
        raise OperationsError("Parameters to drop must be a list of at most 100 non-empty names without whitespace or control characters.")
    return list(value)


def view(config):
    rows = []
    for index, row in enumerate(config["model_list"]):
        params = row["litellm_params"]
        rows.append({
            "index": index, "model_name": row.get("model_name", ""),
            "model": params.get("model", ""),
            "api_base": public_url(params.get("api_base")),
            "additional_drop_params": drop_parameters(params.get("additional_drop_params")),
            "credentials_configured": bool(params.get("api_key")) and params.get("api_key") != "not-needed",
        })
    router = config.get("router_settings", {})
    aliases = router.get("model_group_alias", {})
    if not isinstance(aliases, dict):
        raise OperationsError("Existing model_group_alias must be a mapping.")
    alias_view = {}
    for name, target in aliases.items():
        alias_view[name] = target.get("model", "") if isinstance(target, dict) else target
    fallbacks = router.get("fallbacks", [])
    if not isinstance(fallbacks, list):
        raise OperationsError("Existing fallbacks must be a list.")
    return {"models": rows, "aliases": alias_view,
            "routing_strategy": router.get("routing_strategy", "simple-shuffle"),
            "fallbacks": copy.deepcopy(fallbacks)}


def snapshot(raw):
    return {"configured": True, "revision": revision(raw), "draft": view(load_config(raw)),
            "strategies": list(STRATEGIES)}


def candidate(raw, draft):
    config = load_config(raw)
    original_view = view(config)
    if not isinstance(draft, dict) or not isinstance(draft.get("models"), list):
        raise OperationsError("A model draft is required.")
    if not 1 <= len(draft["models"]) <= 500:
        raise OperationsError("Keep between 1 and 500 model deployments.")
    original_rows = config["model_list"]
    new_rows, seen_indexes = [], set()
    for requested in draft["models"]:
        if not isinstance(requested, dict):
            raise OperationsError("Each deployment must be an object.")
        index = requested.get("index")
        if index is not None:
            if (type(index) is not int or index < 0 or index >= len(original_rows)
                    or index in seen_indexes):
                raise OperationsError("An existing deployment index is invalid or repeated.")
            seen_indexes.add(index)
            row = copy.deepcopy(original_rows[index])
        else:
            row = {"litellm_params": {}}
        row["model_name"] = identifier(requested.get("model_name"), "Public model name")
        params = row["litellm_params"]
        model = identifier(requested.get("model"), "Backend model")
        if index is None and not model.startswith(("openai/", "ollama/", "ollama_chat/")):
            raise OperationsError("New deployments support openai/, ollama/ or ollama_chat/ backends.")
        params["model"] = model
        if "additional_drop_params" in requested:
            dropped = drop_parameters(requested["additional_drop_params"])
            previous = drop_parameters(params.get("additional_drop_params"))
            if dropped != previous:
                if dropped:
                    params["additional_drop_params"] = dropped
                else:
                    params.pop("additional_drop_params", None)
        base = requested.get("api_base", "")
        if base == UNCHANGED:
            if index is None or original_view["models"][index]["api_base"] != UNCHANGED:
                raise OperationsError("A protected endpoint can only be retained on its original deployment.")
        elif base:
            if public_url(base) == UNCHANGED:
                raise OperationsError("Endpoints must be HTTP(S) URLs without embedded credentials or query strings.")
            params["api_base"] = base
        else:
            params.pop("api_base", None)
        if index is None:
            if not base:
                raise OperationsError("New local deployments require an endpoint URL.")
            if model.startswith("openai/"):
                params["api_key"] = "not-needed"
        new_rows.append(row)
    config["model_list"] = new_rows
    names = {row["model_name"] for row in new_rows}
    aliases = draft.get("aliases", {})
    if not isinstance(aliases, dict) or len(aliases) > 500:
        raise OperationsError("Aliases must be a mapping of names to model groups.")
    old_aliases = config.get("router_settings", {}).get("model_group_alias", {})
    updated_aliases = {}
    for name, target in aliases.items():
        name = identifier(name, "Alias")
        target = identifier(target, "Alias target")
        if name in names or target not in names:
            raise OperationsError("Aliases must have distinct names and target an existing model group.")
        old = old_aliases.get(name)
        if isinstance(old, dict):
            updated_aliases[name] = copy.deepcopy(old)
            updated_aliases[name]["model"] = target
        else:
            updated_aliases[name] = target
    strategy = draft.get("routing_strategy", "simple-shuffle")
    if strategy not in STRATEGIES:
        raise OperationsError("Choose a supported LiteLLM routing strategy.")
    fallbacks = draft.get("fallbacks", [])
    if not isinstance(fallbacks, list) or len(fallbacks) > 500:
        raise OperationsError("Fallbacks must be a list of model-group mappings.")
    edges = {}
    for item in fallbacks:
        if not isinstance(item, dict) or len(item) != 1:
            raise OperationsError("Each fallback must map one model group to a list of targets.")
        source, targets = next(iter(item.items()))
        if source not in names or source in edges or not isinstance(targets, list):
            raise OperationsError("Fallback sources must be unique existing model groups.")
        if (not all(isinstance(target, str) and target in names and target != source for target in targets)
                or len(targets) != len(set(targets))):
            raise OperationsError("Fallback targets must be distinct existing model groups.")
        edges[source] = targets
    visited, visiting = set(), set()
    def check_cycle(node):
        if node in visiting:
            raise OperationsError("Fallback routing must not contain cycles.")
        if node in visited:
            return
        visiting.add(node)
        for target in edges.get(node, []):
            check_cycle(target)
        visiting.remove(node)
        visited.add(node)
    for name in edges:
        check_cycle(name)
    router = config.get("router_settings", {})
    for key, value, previous in (
        ("model_group_alias", updated_aliases, original_view["aliases"]),
        ("routing_strategy", strategy, original_view["routing_strategy"]),
        ("fallbacks", fallbacks, original_view["fallbacks"]),
    ):
        if value != previous:
            router[key] = value
    if router:
        config["router_settings"] = router
    # An unchanged draft must not rewrite formatting or comments.
    if view(config) == original_view:
        return raw, config
    stream = io.StringIO()
    yaml_codec().dump(config, stream)
    result = stream.getvalue().encode("utf-8")
    if len(result) > MAX_CONFIG_BYTES:
        raise OperationsError("The edited configuration exceeds the 1 MiB limit.")
    return result, config


def review(raw, draft):
    edited, config = candidate(raw, draft)
    before = json.dumps(view(load_config(raw)), indent=2, ensure_ascii=False).splitlines()
    after = json.dumps(view(config), indent=2, ensure_ascii=False).splitlines()
    diff = "\n".join(difflib.unified_diff(before, after, fromfile="current", tofile="proposed", lineterm=""))
    return {"revision": revision(raw), "review_digest": revision(edited),
            "changed": edited != raw, "diff": diff, "restart_required": edited != raw,
            "warnings": ["Applying restarts LiteLLM and can interrupt active requests."]}
