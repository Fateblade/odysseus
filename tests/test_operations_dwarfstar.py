"""DwarfStar discovery and real helper/worker transport against a local API fixture."""

import asyncio
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from services.operations import dwarfstar, jobs
from services.operations.litellm import HostOperations
from services.operations.litellm_config import OperationsError
from services.operations.management import Management


CATALOG = {"object": "list", "data": [
    {"id": alias, "name": "DeepSeek V4 Flash 0731", "context_length": 4096, "owned_by": "ds4.c"}
    for alias in ("deepseek-v4-flash", "deepseek-v4-pro")
]}
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, None))
            value = CATALOG if self.path == "/v1/models" else {"models": [], "status": "ok", "loaded": False}
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            events = [
                {"choices": [{"delta": {"content": "A multi-token chunk"}}]},
                {"choices": [{"delta": {"content": "."}}]},
                {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
            ]
            content = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(content.encode())))
            self.end_headers()
            self.wfile.write(content.encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_discovery_reports_actual_model_and_context_not_two_alias_models(api_server):
    base, requests = api_server
    result = dwarfstar.inventory(base + "/")
    assert result["status"] == "ok"
    assert result["loaded"] is True
    assert result["model"] == "DeepSeek V4 Flash 0731"
    assert result["max_context"] == 4096
    assert result["aliases"] == ["deepseek-v4-flash", "deepseek-v4-pro"]
    assert requests == [("/v1/models", None)]
    assert "gpu_bytes" not in result


def test_disabled_discovery_makes_no_network_request(monkeypatch):
    def unexpected(url):
        pytest.fail("Disabled DwarfStar must not be probed")

    monkeypatch.setattr(dwarfstar, "get_json", unexpected)
    assert dwarfstar.inventory("")["status"] == "disabled"
    assert "loaded" not in dwarfstar.inventory("")


@pytest.mark.parametrize("catalog", [{}, [], {"data": []}, {"data": [None]}, {"data": [{"id": 42}]}])
def test_invalid_catalog_never_claims_unloaded_or_ready(monkeypatch, catalog):
    monkeypatch.setattr(dwarfstar, "get_json", lambda url: catalog)
    result = dwarfstar.inventory("http://dwarfstar")
    assert result["status"] == "unavailable"
    assert result["configured"] is True
    assert "loaded" not in result


def test_alias_without_model_metadata_does_not_identify_loaded_checkpoint(monkeypatch):
    monkeypatch.setattr(dwarfstar, "get_json", lambda url: {"data": [{"id": "deepseek-v4-pro", "context_length": True}]})
    result = dwarfstar.inventory("http://dwarfstar")
    assert result["model"] is None
    assert result["max_context"] is None


def test_probe_failure_does_not_expose_exception_or_claim_unloaded(monkeypatch):
    def offline(url):
        raise OSError("private diagnostic")

    monkeypatch.setattr(dwarfstar, "get_json", offline)
    result = dwarfstar.inventory("http://dwarfstar")
    assert result["status"] == "unavailable"
    assert "loaded" not in result
    assert "private diagnostic" not in json.dumps(result)


def test_host_transport_passes_optional_endpoint_through_real_helper(api_server):
    base, requests = api_server
    host = HostOperations({"ODYSSEUS_OPERATIONS_HOST_HELPER": str(ROOT / "scripts/odysseus-litellm.py"),
                           "ODYSSEUS_OPERATIONS_STRATA_URL": base, "ODYSSEUS_OPERATIONS_OLLAMA_URL": base,
                           "ODYSSEUS_OPERATIONS_DWARFSTAR_URL": base})
    result = asyncio.run(host.run("residency"))
    assert result["dwarfstar"]["model"] == "DeepSeek V4 Flash 0731"
    assert result["strata"]["loaded"] is False
    assert result["ollama"]["loaded_available"] is True
    assert ("/v1/models", None) in requests
    assert all(body is None for _, body in requests)


def test_detached_worker_uses_dwarfstar_endpoint_and_backend_token_usage(api_server, tmp_path):
    base, requests = api_server
    specification = {"kind": "benchmark", "backend": "dwarfstar", "model": "deepseek-v4-flash",
                     "prompt": "Test", "runs": 1, "max_tokens": 32, "warmup": False}
    (tmp_path / "job.json").write_text(json.dumps({"specification": specification}))
    process = subprocess.run([sys.executable, str(ROOT / "scripts/odysseus-operations-worker.py"),
                              str(tmp_path), "", "http://127.0.0.1:1", "http://127.0.0.1:1",
                              "--dwarfstar-url", base, "--execute"], capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["backend"] == "dwarfstar"
    assert result["thinking"] is False
    assert result["samples"][0]["output_tokens"] == 7
    assert result["samples"][0]["prompt_tokens"] == 12
    assert result["summary"]["decode_tps"] is None
    assert result["summary"]["prefill_tps"] is None
    assert result["summary"]["end_to_end_tps"] > 0
    assert requests[0][0] == "/v1/chat/completions"
    assert requests[0][1]["think"] is False
    assert requests[0][1]["stream_options"] == {"include_usage": True}


def test_dwarfstar_benchmark_requires_configuration_and_does_not_route_to_strata(tmp_path):
    config = tmp_path / "management.json"
    config.write_text(json.dumps({"jobs_directory": str(tmp_path / "jobs")}))
    management = Management(str(config), "http://strata", "http://ollama")
    with pytest.raises(OperationsError, match="Configure the DwarfStar endpoint"):
        jobs.execute(management, {"action": "job_start", "kind": "benchmark", "backend": "dwarfstar"})


def test_monitoring_configuration_does_not_grant_service_control():
    management = Management(None, "http://strata", "http://ollama", "http://dwarfstar")
    catalog = management.catalog()
    assert "dwarfstar" in catalog["unconfigured_services"]
    assert "dwarfstar" not in catalog["services"]
    with pytest.raises(OperationsError, match="Configure an explicit systemd unit"):
        management.execute({"target": "service", "name": "dwarfstar", "operation": "start"})



@pytest.mark.parametrize("state,code,expected", [("inactive", 3, "stopped"), ("failed", 3, "unavailable"),
                                                 ("active", 0, "unavailable"), ("unknown", 4, "unavailable")])
def test_offline_inventory_uses_confirmed_service_state(monkeypatch, state, code, expected):
    from types import SimpleNamespace
    def offline(url):
        raise OSError("Connection refused")
    monkeypatch.setattr(dwarfstar, "get_json", offline)
    calls = []
    def service(name, operation, check):
        calls.append((name, operation, check))
        return SimpleNamespace(returncode=code, stdout=state.encode())
    result = dwarfstar.inventory("http://native", SimpleNamespace(service=service))
    assert result["status"] == expected
    assert calls == [("dwarfstar", "is-active", False)]
    if expected == "stopped":
        assert result["loaded"] is False


def test_offline_inventory_keeps_warning_when_service_state_cannot_be_read(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(dwarfstar, "get_json", lambda url: {})
    def denied(*args, **kwargs):
        raise OSError("No systemd access")
    assert dwarfstar.inventory("http://native", SimpleNamespace(service=denied))["status"] == "unavailable"
