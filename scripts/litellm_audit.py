"""LiteLLM 1.90 callback; mount alongside odysseus_audit_store.py in the gateway."""

import os
import uuid
import sys
from pathlib import Path

from litellm.integrations.custom_logger import CustomLogger
sys.path.insert(0, str(Path(__file__).resolve().parent))
from odysseus_audit_store import emit


def mapping(value):
    return value if isinstance(value, dict) else {}


def field(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def token_count(usage, primary, alternative):
    value = field(usage, primary)
    return field(usage, alternative) if value is None else value


def metadata(data):
    params = mapping(data.get("litellm_params"))
    return mapping(params.get("metadata") or params.get("litellm_metadata") or data.get("metadata") or data.get("litellm_metadata"))


class RequestAudit(CustomLogger):
    def __init__(self):
        super().__init__()
        self.folder = os.environ.get("ODYSSEUS_AUDIT_DIRECTORY", "/var/log/odysseus-audit")
        self.capture = os.environ.get("ODYSSEUS_AUDIT_CAPTURE_CONTENT", "false").lower() == "true"

    def write(self, event, data, **extra):
        params = mapping(data.get("litellm_params"))
        context = mapping(metadata(data).get("odysseus_audit"))
        values = {key: context.get(key) for key in ("request_id", "actor", "client_ip", "client_label", "user_agent",
                                                   "key_alias", "claimed_user", "client_request_id", "requested_model")}
        values.update(source="litellm", model=data.get("model"), backend=params.get("custom_llm_provider"),
                      endpoint=params.get("api_base") or data.get("api_base"), stream=data.get("stream"),
                      call_type=data.get("call_type"))
        if not values.get("request_id"):
            values["request_id"] = data.get("litellm_call_id")
        values.update(extra)
        return emit(self.folder, event, **values)

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        meta = metadata(data)
        headers = {str(key).lower(): value for key, value in mapping(mapping(data.get("proxy_server_request")).get("headers")).items()}
        actor = field(user_api_key_dict, "user_id")
        alias = field(user_api_key_dict, "key_alias")
        context = {"request_id": uuid.uuid4().hex, "actor": actor or ("key:" + str(alias) if alias else "unidentified gateway client"),
                   "key_alias": alias, "client_ip": meta.get("requester_ip_address"),
                   "user_agent": headers.get("user-agent") or meta.get("user_agent"),
                   "client_label": headers.get("x-client-name"), "claimed_user": data.get("user"),
                   "client_request_id": headers.get("x-request-id"), "requested_model": data.get("model")}
        variable = "litellm_metadata" if "litellm_metadata" in data else "metadata"
        trusted = {**meta, "odysseus_audit": context}
        for key in ("metadata", "litellm_metadata"):
            if key == variable or key in data:
                data[key] = trusted
        params = mapping(data.get("litellm_params"))
        for key in ("metadata", "litellm_metadata"):
            if key in params:
                params[key] = trusted
        self.write("request.received", data, call_type=call_type,
                   content={key: data[key] for key in ("messages", "input", "prompt", "tools") if key in data} if self.capture else None)
        return data

    async def async_log_pre_api_call(self, model, messages, kwargs):
        self.write("request.dispatched", {**kwargs, "model": model})

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        usage = field(response_obj, "usage") or {}
        response = kwargs.get("complete_streaming_response") or response_obj
        usage = field(response, "usage") or usage
        try:
            duration = (end_time - start_time).total_seconds() * 1000
        except (TypeError, AttributeError):
            duration = None
        choices = field(response, "choices")
        if choices is not None:
            choices = [choice.model_dump() if hasattr(choice, "model_dump") else choice for choice in choices]
        response_content = {"choices": choices} if choices is not None else {"output": field(response, "output"), "data": field(response, "data")}
        self.write("request.succeeded", kwargs, duration_ms=duration, cache_hit=kwargs.get("cache_hit"),
                   prompt_tokens=token_count(usage, "prompt_tokens", "input_tokens"),
                   output_tokens=token_count(usage, "completion_tokens", "output_tokens"),
                   content=response_content if self.capture else None)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        error = kwargs.get("exception") or response_obj
        self.write("request.attempt_failed", kwargs, error_type=type(error).__name__, status_code=field(error, "status_code"))

    async def async_post_call_failure_hook(self, request_data, original_exception, user_api_key_dict, traceback_str=None):
        self.write("request.failed", request_data, error_type=type(original_exception).__name__, status_code=field(original_exception, "status_code"))


audit_logger = RequestAudit()
