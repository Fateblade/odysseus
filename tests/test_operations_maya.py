"""Maya's native catalog, context and streaming contracts across helper processes."""

import asyncio
import importlib.util
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.operations import jobs, maya, maya_config
from services.operations.litellm import HostOperations
from services.operations.litellm_config import OperationsError
from services.operations.management import Management


ROOT = Path(__file__).resolve().parents[1]
MODEL = "deepseek-v4.1-flash"


@pytest.mark.parametrize("content, message", [
    ('{"services": {},}\n', "invalid JSON at line 1"),
    ('{}\n{"private": "do-not-expose"}', "invalid JSON at line 2"),
    ('[]', "must be JSON objects"),
    ('{"services": []}', "must be JSON objects"),
    ('{"services": {"maya_glm": null}}', "unit-name string"),
])
def test_helper_reports_management_configuration_errors(tmp_path, content, message):
    settings = tmp_path / "management.json"
    settings.write_text(content, encoding="utf-8")
    host = HostOperations({"ODYSSEUS_OPERATIONS_HOST_HELPER": str(ROOT / "scripts/odysseus-litellm.py"),
                           "ODYSSEUS_OPERATIONS_MANAGEMENT_CONFIG": str(settings)})
    with pytest.raises(OperationsError, match=message) as failure:
        asyncio.run(host.run("management"))
    assert failure.value.status == 503
    assert "do-not-expose" not in str(failure.value)


@pytest.fixture
def maya_server():
    requests = []
    state = {"context": 4096, "unloaded": False, "health": "ok", "reload": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, None))
            if self.path == "/health":
                value = {"status": state["health"], "unloaded": state["unloaded"], "max_context": state["context"],
                         "model": state.get("model", MODEL), "version": "1.1.22"}
            elif self.path == "/v1/models":
                value = {"data": [{"id": state.get("model", MODEL), "status": {"value": "unloaded" if state["unloaded"] else "loaded"},
                                   "meta": {"n_ctx": state["context"]}}]}
            elif self.path == "/api/context":
                value = {"context": state["context"], "min": 1024, "max": 1048576, "reload": state["reload"]}
            else:
                value = {"models": [], "status": "ok", "loaded": False}
            self.reply(json.dumps(value).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            if self.path == "/api/tokens":
                self.reply(json.dumps({"tokens": 13, "max_context": state["context"], "approximate": False}).encode())
                return
            if self.path == "/api/context":
                assert self.headers["Origin"] == f"http://{self.headers['Host']}"
                state["context"] = body["max_context"]
                state["reload"] = {"state": "done", "error": None}
                self.reply(json.dumps({"reload": state["reload"]}).encode(), 202)
                return
            if body.get("tools"):
                message = ({"role": "assistant", "content": "5"} if body["tool_choice"] == "none" else
                           {"role": "assistant", "content": None, "tool_calls": [{"id": "fixed-call-id", "type": "function",
                            "function": {"name": "add", "arguments": '{"a":2,"b":3}'}}]})
                self.reply(json.dumps({"choices": [{"message": message}]}).encode())
                return
            events = [{"choices": [{"delta": {"content": "Multiple tokens in one chunk."}}]},
                      {"choices": [], "usage": {"prompt_tokens": 13, "completion_tokens": 8},
                       "timings": {"predicted_per_second": 12.5, "prompt_per_second": 33.0,
                                   "draft_n": 10, "draft_n_accepted": 6}}]
            self.reply(("".join("data: " + json.dumps(event) + "\n\n" for event in events)
                        + "data: [DONE]\n\n").encode())

        def reply(self, raw, status=200):
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_native_discovery_and_unloaded_state(maya_server):
    base, requests, state = maya_server
    ready = maya.inventory(base)
    assert ready["loaded"] is True and ready["max_context"] == 4096
    assert ready["aliases"] == [MODEL] and ready["version"] == "1.1.22"
    state["unloaded"] = True
    assert maya.inventory(base)["loaded"] is False
    state.update(unloaded=False, health="reloading")
    assert maya.inventory(base)["status"] == "degraded"
    assert all(body is None for _, body in requests)


def test_disabled_endpoint_does_not_probe(monkeypatch):
    monkeypatch.setattr(maya, "get_json", lambda _: pytest.fail("Disabled endpoint was probed"))
    assert maya.inventory("")["status"] == "disabled"


@pytest.mark.parametrize("value", [{}, [], {"status": "ok"}, {"data": [None]}])
def test_invalid_discovery_never_claims_residency(monkeypatch, value):
    monkeypatch.setattr(maya, "get_json", lambda _: value)
    assert "loaded" not in maya.inventory("http://maya")


def test_host_helper_propagates_separate_maya_endpoint(maya_server):
    base, requests, _ = maya_server
    host = HostOperations({"ODYSSEUS_OPERATIONS_HOST_HELPER": str(ROOT / "scripts/odysseus-litellm.py"),
                           "ODYSSEUS_OPERATIONS_STRATA_URL": base, "ODYSSEUS_OPERATIONS_OLLAMA_URL": base,
                           "ODYSSEUS_OPERATIONS_MAYA_URL": base})
    result = asyncio.run(host.run("residency"))
    assert result["maya"]["model"] == MODEL
    assert result["dwarfstar"]["status"] == "disabled"
    assert all(body is None for _, body in requests)


def test_worker_keeps_native_timings_usage_acceptance_and_journal(maya_server, tmp_path):
    base, requests, _ = maya_server
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"audit_directory": str(tmp_path / "audit")}))
    specification = {"kind": "benchmark", "backend": "maya", "model": MODEL, "prompt": "Fixed task",
                     "runs": 1, "max_tokens": 32, "warmup": False}
    (tmp_path / "job.json").write_text(json.dumps({"specification": specification}))
    process = subprocess.run([sys.executable, str(ROOT / "scripts/odysseus-operations-worker.py"), str(tmp_path),
                              str(settings), "http://127.0.0.1:1", "http://127.0.0.1:1", "--maya-url", base, "--execute"],
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "result.json").read_text())
    sample = result["samples"][0]
    assert sample["output_tokens"] == 8 and sample["prompt_tokens"] == 13
    assert sample["decode_tps"] == 12.5 and sample["prefill_tps"] == 33
    assert sample["speculative_acceptance"] == 0.6
    assert result["thinking"] is False
    assert requests[0][1]["reasoning_effort"] == "none"
    assert "think" not in requests[0][1]
    audit = "".join(path.read_text() for path in (tmp_path / "audit").glob("*.jsonl"))
    assert '"backend": "maya"' in audit and "Fixed task" in audit and "Multiple tokens" in audit


def test_benchmark_rejects_unconfigured_maya(tmp_path):
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"jobs_directory": str(tmp_path / "jobs")}))
    with pytest.raises(OperationsError, match="Configure the Maya endpoint"):
        jobs.execute(Management(str(settings), "", ""), {"action": "job_start", "kind": "benchmark", "backend": "maya"})


def test_context_review_rejects_changed_state_and_excess_context(maya_server):
    base, requests, state = maya_server
    management = Management(None, "", "", maya_url=base)
    current = maya_config.snapshot(management)
    with pytest.raises(OperationsError, match="between"):
        maya_config.review(management, {"revision": current["revision"], "context": 8192})
    reviewed = maya_config.review(management, {"revision": current["revision"], "context": 2048})
    state["context"] = 3072
    with pytest.raises(OperationsError, match="state changed"):
        maya_config.run(management, reviewed, None)
    assert all(body is None for _, body in requests)


def test_context_native_reload_verifies_model_and_context(maya_server):
    base, requests, _ = maya_server
    management = Management(None, "", "", maya_url=base)
    current = maya_config.snapshot(management)
    reviewed = maya_config.review(management, {"revision": current["revision"], "context": 2048})
    result = maya_config.run(management, reviewed, None)
    assert result == {"active_context": 2048, "model": MODEL}
    assert ("/api/context", {"max_context": 2048}) in requests


def test_context_apply_requires_review_and_existing_job_queue(maya_server, monkeypatch):
    base, _, _ = maya_server
    management = Management(None, "", "", maya_url=base)
    current = maya_config.snapshot(management)
    reviewed = maya_config.review(management, {"revision": current["revision"], "context": 2048})
    with pytest.raises(OperationsError, match="Review the exact"):
        maya_config.enqueue_apply(management, {**reviewed, "review_digest": "changed"})
    def occupied(*_):
        raise OperationsError("A host job is already active.", 409)
    monkeypatch.setattr(jobs, "enqueue", occupied)
    with pytest.raises(OperationsError, match="already active"):
        maya_config.enqueue_apply(management, reviewed)


def test_service_start_refuses_competing_models():
    management = SimpleNamespace(residency=lambda: {"strata": {"loaded": True}, "ollama": {}, "dwarfstar": {}},
                                 service=lambda *_: pytest.fail("Competing service must not be stopped"))
    with pytest.raises(OperationsError, match="Unload competing"):
        maya.run_service(management, {"operation": "start"})


def test_monitoring_does_not_grant_service_permissions():
    management = Management(None, "", "", maya_url="http://maya")
    assert "maya" in management.catalog()["unconfigured_services"]
    with pytest.raises(OperationsError, match="allowlisted"):
        management.execute({"target": "service", "name": "maya", "operation": "start"})


def test_service_job_waits_for_readiness(maya_server):
    base, requests, _ = maya_server
    commands = []
    management = SimpleNamespace(maya_url=base,
                                 residency=lambda: {"strata": {}, "ollama": {}, "dwarfstar": {}},
                                 service=lambda *args: commands.append(args) or SimpleNamespace(stdout=b"", stderr=b""))
    result = maya.run_service(management, {"operation": "start"})
    assert result["note"] == "Readiness verified"
    assert commands == [("maya", "start")]
    assert ("/health", None) in requests


def test_context_failure_does_not_expose_native_error(maya_server, monkeypatch):
    base, _, state = maya_server
    management = Management(None, "", "", maya_url=base)
    current = maya_config.snapshot(management)
    reviewed = maya_config.review(management, {"revision": current["revision"], "context": 2048})
    from services.operations import management as management_module
    def failed(*args):
        state["reload"] = {"state": "failed", "error": "private runtime details"}
    monkeypatch.setattr(management_module, "api", failed)
    with pytest.raises(OperationsError, match="native rollback") as error:
        maya_config.run(management, reviewed, None)
    assert "private" not in str(error.value)


def test_maya_context_routes_require_admin_and_reject_invalid_json(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes import operations_routes
    calls = []
    class Host:
        async def run(self, action, values):
            calls.append(action)
            return {"configured": True}
    app = FastAPI()
    app.include_router(operations_routes.setup_operations_routes(Host()))
    monkeypatch.setattr(operations_routes, "require_admin", lambda _: None)
    with TestClient(app) as client:
        assert client.get('/api/operations/maya/config').status_code == 200
        assert client.post('/api/operations/maya/preview', json={}).status_code == 200
        assert client.post('/api/operations/maya/apply', json={}).status_code == 200
        assert calls == ['maya_config', 'maya_preview', 'maya_apply']
        assert client.post('/api/operations/maya/apply', content='{}').status_code == 415
        def denied(_):
            raise HTTPException(403, "Admin only")
        monkeypatch.setattr(operations_routes, "require_admin", denied)
        assert client.get('/api/operations/maya/config').status_code == 403
        assert client.post('/api/operations/maya/apply', json={}).status_code == 403


def test_artifact_verifier_rejects_same_size_corruption(tmp_path):
    spec = importlib.util.spec_from_file_location("verify_maya", ROOT / "scripts/odysseus-verify-maya-xs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tmp_path / "weights"
    path.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="SHA-256"):
        module.verify(tmp_path, {"files": {"weights": {"bytes": 5, "sha256": "0" * 64}}})


@pytest.mark.parametrize("backend", ["maya", "dwarfstar"])
def test_comparison_cli_uses_identical_fixtures_and_preserves_tool_ids(maya_server, tmp_path, backend):
    base, _, _ = maya_server
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"audit_directory": str(tmp_path / "audit")}))
    output = tmp_path / "comparison.json"
    process = subprocess.run([sys.executable, str(ROOT / "scripts/odysseus-evaluate-deepseek.py"),
                              "--backend", backend, "--base-url", base, "--model", MODEL,
                              "--label", "fixture", "--management-config", str(settings), "--output", str(output)],
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(output.read_text())
    assert len(result["fixtures"]) == 3
    assert all(len(row["result"]["samples"]) == 3 for row in result["fixtures"])
    assert result["tools"]["arguments_correct"] is True
    assert result["tools"]["final_content"] == "5"
    transcript = result["tools"]["transcript"]
    assert len(transcript[0]["request"]["messages"]) == 1
    assert transcript[1]["request"]["messages"][-1]["tool_call_id"] == "fixed-call-id"



def test_real_helper_reads_selected_context_and_exact_budget_without_generation(maya_server):
    base, requests, state = maya_server
    state["context"] = 128000
    host = HostOperations({"ODYSSEUS_OPERATIONS_HOST_HELPER": str(ROOT / "scripts/odysseus-litellm.py"),
                           "ODYSSEUS_OPERATIONS_MAYA_URL": base})
    result = asyncio.run(host.run("benchmark_limits", {"backend": "maya", "model": MODEL, "validate_tasks": True,
                                                      "prompt": "Example", "runs": 1, "warmup": True}))
    assert result["context_tokens"] == 128000 and result["max_output_tokens"] == 127979 and result["exact"]
    assert [path for path, body in requests if body is not None] == ["/api/tokens"]



def test_glm_profile_transport_and_exact_limits_do_not_replace_ds41f(maya_server, tmp_path):
    base, requests, state = maya_server
    state["model"] = "glm-5.3-flash"
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"maya_glm_setup": {"maximum_context": 32768},
                                   "maya_setup": {"config_file": "/must/not/read/ds41f.json"}}))
    host = HostOperations({"ODYSSEUS_OPERATIONS_HOST_HELPER": str(ROOT / "scripts/odysseus-litellm.py"),
                           "ODYSSEUS_OPERATIONS_MANAGEMENT_CONFIG": str(settings),
                           "ODYSSEUS_OPERATIONS_MAYA_GLM_URL": base})
    residency = asyncio.run(host.run("residency"))
    assert residency["maya_glm"]["model"] == "glm-5.3-flash"
    assert residency["maya"]["status"] == "disabled"
    config = asyncio.run(host.run("maya_glm_config"))
    assert config["configured"] and config["maximum_context"] == 32768
    budget = asyncio.run(host.run("benchmark_limits", {"backend": "maya_glm", "model": "glm-5.3-flash",
                                                        "validate_tasks": True, "prompt": "Example", "runs": 1}))
    assert budget["exact"] and budget["max_output_tokens"] == 4096 - 13 - 8
    assert [path for path, body in requests if body is not None] == ["/api/tokens"]


def test_glm_worker_uses_isolated_endpoint_native_counts_and_audit(maya_server, tmp_path):
    base, requests, state = maya_server
    state["model"] = "glm-5.3-flash"
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"audit_directory": str(tmp_path / "audit")}))
    specification = {"kind": "benchmark", "backend": "maya_glm", "model": "glm-5.3-flash", "prompt": "GLM task",
                     "runs": 1, "max_tokens": 32, "warmup": False}
    (tmp_path / "job.json").write_text(json.dumps({"specification": specification}))
    process = subprocess.run([sys.executable, str(ROOT / "scripts/odysseus-operations-worker.py"), str(tmp_path),
                              str(settings), "http://127.0.0.1:1", "http://127.0.0.1:1", "--maya-glm-url", base, "--execute"],
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["backend"] == "maya_glm" and result["thinking"] is False
    assert result["samples"][0]["output_tokens"] == 8 and result["samples"][0]["decode_tps"] == 12.5
    assert requests[0][1]["reasoning_effort"] == "none"
    assert '"backend": "maya_glm"' in "".join(path.read_text() for path in (tmp_path / "audit").glob("*.jsonl"))


def test_glm_context_reload_changes_only_its_native_endpoint(maya_server, tmp_path):
    base, requests, state = maya_server
    state["model"] = "glm-5.3-flash"
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"maya_glm_setup": {"maximum_context": 32768}}))
    management = Management(str(settings), "", "", "", "", base)
    current = maya_config.snapshot(management, backend="maya_glm")
    reviewed = maya_config.review(management, {"revision": current["revision"], "context": 8192}, backend="maya_glm")
    result = maya_config.run(management, {**reviewed, "backend": "maya_glm"}, tmp_path)
    assert result["active_context"] == 8192
    assert ("/api/context", {"max_context": 8192}) in requests
    assert management.maya_url == ""


@pytest.mark.parametrize("backend,other", [("maya", "maya_glm"), ("maya_glm", "maya")])
def test_maya_profiles_refuse_to_start_over_each_other(backend, other):
    def forbidden(*args, **kwargs):
        pytest.fail("No competing service may be stopped automatically")
    live = {"strata": {}, "ollama": {}, "dwarfstar": {}, other: {"loaded": True}}
    management = SimpleNamespace(maya_url="http://ds", maya_glm_url="http://glm", residency=lambda: live, service=forbidden)
    with pytest.raises(OperationsError, match="other Maya"):
        maya.run_service(management, {"backend": backend, "operation": "start"})


def test_glm_profile_rejects_shared_endpoint_and_unit(tmp_path):
    with pytest.raises(OperationsError, match="own endpoint"):
        Management(None, "", "", "", "http://native", "http://native/")
    settings = tmp_path / "management.json"
    settings.write_text(json.dumps({"services": {"maya": "same.service", "maya_glm": "same.service"}}))
    with pytest.raises(OperationsError, match="different systemd units"):
        Management(str(settings), "", "", "", "http://ds", "http://glm")


def test_glm_discovery_rejects_deepseek_on_the_glm_profile(maya_server):
    base, requests, state = maya_server
    result = maya.glm_inventory(base)
    assert result["status"] == "degraded" and result["loaded"] is True



def test_glm_management_actions_keep_profile_attribution():
    from services.operations.audit import request_fields
    assert request_fields({"target": "service", "name": "maya_glm", "operation": "start"})["backend"] == "maya_glm"
    assert request_fields({"action": "maya_glm_apply", "context": 8192})["backend"] == "maya_glm"
