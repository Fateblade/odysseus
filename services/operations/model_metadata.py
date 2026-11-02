"""Deployment metadata resolution and conservative public route envelopes."""

import copy
import hashlib
import json
import time

from services.operations.litellm_config import OperationsError

LIMITS = ("context_window", "max_input_tokens", "max_output_tokens")
FLAGS = ("supports_function_calling", "supports_vision", "supports_reasoning")
FIELDS = (*LIMITS, *FLAGS, "mode")
MODES = ("chat", "embedding", "completion")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fingerprint(row):
    params = row.get("litellm_params", row)
    return digest({"model": params.get("model"), "api_base": params.get("api_base", "")})


def deployment_id(row, index):
    info = row.get("model_info") or {}
    return (info.get("odysseus") or {}).get("deployment_id") or digest([index, fingerprint(row)])[:32]


def validate(field, value):
    valid = (type(value) is int and 0 < value <= 2**53 - 1 if field in LIMITS else
             type(value) is bool if field in FLAGS else value in MODES if isinstance(value, str) else False)
    if not valid:
        raise OperationsError(f"Invalid metadata value for {field}.")
    return value


def resolve(detected, overrides, strict=True):
    if not isinstance(overrides, dict) or set(overrides) - set(FIELDS):
        raise OperationsError("Metadata overrides contain unsupported fields.")
    values, origins, conflicts = {}, {}, []
    for field in FIELDS:
        observed = detected.get(field)
        if observed is not None:
            validate(field, observed)
        operation = overrides.get(field, {"operation": "inherit"})
        if not isinstance(operation, dict) or operation.get("operation") not in ("inherit", "set", "suppress"):
            raise OperationsError(f"Choose inherit, set or suppress for {field}.")
        action = operation["operation"]
        value = observed
        origin = "detected" if observed is not None else "unknown"
        if action == "suppress":
            value, origin = None, "suppressed"
        elif action == "set":
            value = validate(field, operation.get("value"))
            origin = "operator"
            incompatible = observed is not None and (
                (field in LIMITS and value > observed) or
                (field in FLAGS and value is True and observed is False) or
                (field == "mode" and value != observed))
            if incompatible:
                conflicts.append(field)
                if strict:
                    raise OperationsError(f"{field} exceeds or contradicts the detected supported value ({observed}).")
                value, origin = observed, "detected_conflict"
        values[field], origins[field] = value, origin
    context = values["context_window"]
    if context:
        for field in ("max_input_tokens", "max_output_tokens"):
            if values[field] is not None and values[field] > context:
                if overrides.get(field, {}).get("operation") == "set" and strict:
                    raise OperationsError(f"{field} cannot exceed the effective total context.")
                values[field] = context
    return {"effective": values, "origins": origins, "conflicts": conflicts}


def envelope(members):
    result = {}
    for field in FIELDS:
        values = [member.get(field) for member in members]
        if field in FLAGS and False in values:
            result[field] = False
        elif any(value is None for value in values):
            result[field] = None
        elif field in LIMITS:
            result[field] = min(values)
        elif field in FLAGS:
            result[field] = all(values)
        else:
            result[field] = values[0] if len(set(values)) == 1 else None
    return result


def route_envelopes(config, facts):
    groups = {}
    for row, fact in zip(config["model_list"], facts):
        groups.setdefault(row["model_name"], []).append(fact)
    router = config.get("router_settings", {})
    edges = {}
    for item in router.get("fallbacks", []):
        for name, targets in item.items():
            edges[name] = list(targets)
    # Existing specialized fallback policies also contribute to public guarantees.
    for key in ("context_window_fallbacks", "content_policy_fallbacks"):
        for item in router.get(key, []) or []:
            for name, targets in item.items():
                edges.setdefault(name, []).extend(targets)
    reachable = {}
    def collect(name, visiting):
        if name in visiting:
            raise OperationsError("Metadata routes contain a fallback cycle.")
        if name not in groups:
            raise OperationsError("Metadata fallback targets an unknown model group.")
        if name in reachable:
            return reachable[name]
        members = {name}
        for target in edges.get(name, []):
            members.update(collect(target, visiting | {name}))
        reachable[name] = members
        return members
    return {name: envelope([fact for target in collect(name, set()) for fact in groups[target]]) for name in groups}


def prepare(config, draft, state):
    """Resolve trusted observations; never accept detected values from the browser."""
    records = copy.deepcopy(state.get("deployments", {}))
    facts, ids = [], []
    for index, (row, requested) in enumerate(zip(config["model_list"], draft["models"])):
        metadata = requested.get("metadata")
        if metadata is None:
            raise OperationsError("Each deployment requires an explicit metadata draft. Reload the editor.", 409)
        if not isinstance(metadata, dict):
            raise OperationsError("Deployment metadata must be an object.")
        identity = fingerprint(row)
        record_id = metadata.get("id")
        record = records.get(record_id, {})
        if record and record.get("fingerprint") != identity:
            if not metadata.get("reset_identity"):
                raise OperationsError("Deployment identity changed. Reset its metadata before reviewing.", 409)
            record = {}
        if not record_id or not record:
            record_id = digest([identity, row["model_name"], index])[:32]
        if record_id in ids:
            raise OperationsError("Deployment metadata identifiers must be unique.")
        source_id = metadata.get("source_id")
        if not source_id:
            matches = [key for key, source in state.get("sources", {}).items()
                       if source.get("model") == row["litellm_params"].get("model") and
                       source.get("api_base") == row["litellm_params"].get("api_base") and source.get("status") == "fresh"]
            source_id = matches[0] if len(matches) == 1 else None
        source = state.get("sources", {}).get(source_id) if source_id else None
        if source_id and (not source or source["model"] != row["litellm_params"].get("model") or
                          source["api_base"] != row["litellm_params"].get("api_base")):
            raise OperationsError("Local model selection changed or expired. Refresh and select it again.", 409)
        if source and (source.get("status") != "fresh" or time.time() - source.get("last_success_at", 0) > 900):
            raise OperationsError("Local metadata is unavailable or stale. Refresh discovery before publication.", 409)
        detected = source.get("detected", {}) if source else {}
        overrides = metadata.get("overrides", {})
        resolved = resolve(detected, overrides)
        published_context = source.get("details", {}).get("published_context") if source else None
        context = resolved["effective"]["context_window"]
        if published_context and context and context > published_context:
            raise OperationsError("Context guidance cannot exceed the published model capacity.")
        records[record_id] = {"fingerprint": identity, "source_id": source_id, "overrides": overrides,
                              "effective": resolved["effective"], "origins": resolved["origins"]}
        facts.append(resolved["effective"])
        ids.append(record_id)
    envelopes = route_envelopes(config, facts)
    for row, record_id in zip(config["model_list"], ids):
        if record_id is None:
            continue
        info = row.setdefault("model_info", {})
        effective = envelopes[row["model_name"]]
        for field in FIELDS:
            if field != "context_window":
                if effective[field] is None:
                    info.pop(field, None)
                else:
                    info[field] = effective[field]
        info["odysseus"] = {"schema_version": 1, "deployment_id": record_id,
                            "context_window": effective["context_window"], "guidance_only": True,
                            "effective": effective,
                            "unknown_fields": [field for field, value in effective.items() if value is None]}
        records[record_id]["published"] = effective
    aliases = config.get("router_settings", {}).get("model_group_alias", {})
    public = dict(envelopes)
    for alias, target in aliases.items():
        target = target.get("model") if isinstance(target, dict) else target
        if target in envelopes:
            public[alias] = envelopes[target]
    for row, record_id in zip(config["model_list"], ids):
        if record_id is not None:
            marker = row["model_info"]["odysseus"]
            marker["aliases"] = [alias for alias, target in aliases.items()
                                 if (target.get("model") if isinstance(target, dict) else target) == row["model_name"]]
            marker["evidence"] = "advertised_or_operator_asserted; not_inference_verified"
    return {**state, "deployments": records, "public": public}
