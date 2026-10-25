"""Lifecycle routing holds residency through streams and serialized engine switches."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from services.operations.inference_router import Coordinator, create_app, validate_routes, LeasedResponse
from services.operations.litellm_config import OperationsError


class Engines:
    def __init__(self):
        self.calls = []

    def prepare(self, backend, model):
        self.calls.append(("prepare", backend, model))

    def release(self, backend, model):
        self.calls.append(("release", backend, model))


def fixture(tmp_path, ttl=10):
    host = SimpleNamespace(path=None, settings={"audit_directory": str(tmp_path), "services": {"maya": "maya.service", "maya_glm": "glm.service"}},
                           maya_url="http://127.0.0.1:8092", maya_glm_url="http://127.0.0.1:8093", ollama_url="http://127.0.0.1:11434")
    routes = {"ds": {"backend": "maya", "model": "deepseek-v4.1-flash", "keep_alive_seconds": ttl},
              "glm": {"backend": "maya_glm", "model": "glm-5.3-flash", "keep_alive_seconds": ttl}}
    engines = Engines()
    now = [0.0]
    coordinator = Coordinator(host, routes, engines, clock=lambda: now[0])
    return coordinator, engines, now


@pytest.mark.asyncio
async def test_idle_timer_begins_after_last_completion_and_resets(tmp_path):
    c, engines, now = fixture(tmp_path)
    async with c.lease("ds", "one"):
        now[0] = 100
        await c.expire()
        assert not any(call[0] == "release" for call in engines.calls)
    now[0] = 109
    await c.expire()
    assert c.resident == "ds"
    async with c.lease("ds", "two"):
        now[0] = 110
    now[0] = 119
    await c.expire()
    assert c.resident == "ds"
    now[0] = 120
    await c.expire()
    assert engines.calls[-1] == ("release", "maya", "deepseek-v4.1-flash")
    assert c.resident is None


@pytest.mark.asyncio
async def test_competing_request_waits_and_cancelled_waiter_does_not_load(tmp_path):
    c, engines, now = fixture(tmp_path)
    first = c.lease("ds", "one")
    await first.__aenter__()
    async def run_glm():
        async with c.lease("glm", "two"):
            return c.active
    second = asyncio.create_task(run_glm())
    await asyncio.sleep(.01)
    assert c.queued == 1 and len(engines.calls) == 1
    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second
    assert c.queued == 0
    await first.__aexit__(None, None, None)
    assert await run_glm() == "glm"
    assert engines.calls[-1][1] == "maya_glm"


@pytest.mark.asyncio
async def test_stream_is_forwarded_and_alias_rewritten(tmp_path):
    c, engines, now = fixture(tmp_path)
    captured = []
    async def backend(request):
        captured.append(request)
        assert c.active == "ds"
        return httpx.Response(200, stream=httpx.ByteStream(b'data: {"ok":true}\n\ndata: [DONE]\n\n'), headers={"content-type": "text/event-stream"})
    app = create_app(c, lambda: httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://router") as client:
        result = await client.post("/v1/chat/completions", json={"model": "ds", "messages": [], "stream": True}, headers={"Authorization": "Bearer gateway-key"})
        assert result.status_code == 200 and b"[DONE]" in result.content
        import json
        assert json.loads(captured[0].content)["model"] == "deepseek-v4.1-flash"
        assert "authorization" not in captured[0].headers
        assert c.active is None and not c.lock.locked()
        models = await client.get("/v1/models")
        assert {row["id"] for row in models.json()["data"]} == {"ds", "glm"}
        assert len(engines.calls) == 1
        assert (await client.post("/v1/chat/completions", json={"model": "missing"})).status_code == 400
        assert (await client.post("/api/generate", json={"model": "ds"})).status_code == 400
        assert len(engines.calls) == 1


@pytest.mark.asyncio
async def test_start_failure_releases_lock_and_remains_eligible_for_cleanup(tmp_path):
    c, engines, now = fixture(tmp_path, ttl=0)
    def fail(*args):
        raise OperationsError("start failed", 502)
    engines.prepare = fail
    with pytest.raises(OperationsError):
        async with c.lease("ds", "failed"):
            pytest.fail("Must not dispatch")
    assert not c.lock.locked() and c.active is None
    await c.expire()
    assert c.resident is None


@pytest.mark.asyncio
async def test_backend_error_is_forwarded_and_connections_close(tmp_path):
    c, engines, _ = fixture(tmp_path)
    app = create_app(c, lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(400, stream=httpx.ByteStream(b'{"error":"context exceeded"}')))))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://router") as client:
        result = await client.post("/v1/chat/completions", json={"model": "ds"})
        assert result.status_code == 400 and result.json()["error"] == "context exceeded"
        assert c.active is None and not c.lock.locked()


@pytest.mark.asyncio
async def test_disconnect_keeps_lease_until_engine_finishes(tmp_path):
    c, _, now = fixture(tmp_path)
    complete = asyncio.Event()
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            await complete.wait()
            yield b"last"
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=SlowStream())))
    lease = c.lease("ds", "disconnect")
    await lease.__aenter__()
    upstream = await client.send(client.build_request("POST", "http://backend"), stream=True)
    response = LeasedResponse(upstream, client, lease)
    async def receive():
        return {"type": "http.disconnect"}
    async def send(message):
        pass
    task = asyncio.create_task(response({}, receive, send))
    await asyncio.sleep(.01)
    assert c.active == "ds" and c.lock.locked()
    now[0] = 100
    await c.expire()
    assert c.resident == "ds"
    complete.set()
    await task
    assert c.active is None and c.last_finished == 100


@pytest.mark.parametrize("ttl", [-1, float("nan"), float("inf"), True, "10m"])
def test_invalid_keep_alive_is_rejected(tmp_path, ttl):
    with pytest.raises(ValueError):
        fixture(tmp_path, ttl)


def test_backend_origin_cannot_be_selected_by_a_request(tmp_path):
    c, _, _ = fixture(tmp_path)
    c.management.maya_url = "http://example.com:8092"
    with pytest.raises(ValueError, match="loopback"):
        validate_routes({"bad": {"backend": "maya", "model": "x"}}, c.management)


@pytest.mark.asyncio
async def test_lost_upstream_completion_blocks_switch_and_idle_eviction(tmp_path):
    c, engines, now = fixture(tmp_path, ttl=0)
    c.quarantine(httpx.ReadError("lost stream"))
    with pytest.raises(OperationsError, match="previous upstream request"):
        async with c.lease("glm", "next"):
            pytest.fail("Unsafe switch")
    await c.expire()
    assert not engines.calls and c.status()["status"] == "blocked"


@pytest.mark.asyncio
async def test_restart_restores_idle_deadline_and_uncertain_state(tmp_path):
    c, engines, now = fixture(tmp_path)
    path = tmp_path / "management.json"
    path.write_text('{}')
    c.management.path = path
    c = Coordinator(c.management, c.routes, engines)
    async with c.lease("ds", "one"):
        pass
    resumed = Coordinator(c.management, c.routes, engines)
    assert resumed.resident == "ds" and resumed.last_finished is not None
    resumed.quarantine(httpx.ReadError("lost"))
    blocked = Coordinator(c.management, c.routes, engines)
    assert blocked.uncertain
    with pytest.raises(OperationsError):
        async with blocked.lease("glm", "two"):
            pytest.fail("Unconfirmed previous request")


@pytest.mark.asyncio
async def test_cancelled_start_waits_for_host_operation_before_releasing(tmp_path):
    import threading
    c, engines, now = fixture(tmp_path)
    started, finish = threading.Event(), threading.Event()
    def prepare(*args):
        started.set()
        finish.wait(3)
    engines.prepare = prepare
    async def request():
        async with c.lease("ds", "cancel"):
            pass
    task = asyncio.create_task(request())
    await asyncio.to_thread(started.wait, 3)
    task.cancel()
    await asyncio.sleep(.01)
    assert c.lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not c.lock.locked()


@pytest.mark.asyncio
async def test_real_http_disconnect_does_not_start_next_engine_early(tmp_path):
    import socket
    import threading
    import uvicorn
    c, engines, _ = fixture(tmp_path)
    finish = asyncio.Event()
    loop = asyncio.get_running_loop()
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(finish.wait(), loop))
            engines.calls.append(("engine-finished", "maya", "ds"))
            yield b"last"
    def backend(request):
        body = __import__('json').loads(request.content)
        return httpx.Response(200, stream=SlowStream() if body["model"] == "deepseek-v4.1-flash" else httpx.ByteStream(b"glm"))
    app = create_app(c, lambda: httpx.AsyncClient(transport=httpx.MockTransport(backend)))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = "http://127.0.0.1:" + str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(.01)
        assert server.started
        async with httpx.AsyncClient(timeout=5) as client:
            async with client.stream("POST", address + "/v1/chat/completions", json={"model": "ds"}) as response:
                assert await anext(response.aiter_bytes()) == b"first"
            second = asyncio.create_task(client.post(address + "/v1/chat/completions", json={"model": "glm"}))
            await asyncio.sleep(.05)
            assert not second.done() and len(engines.calls) == 1
            finish.set()
            assert (await second).content == b"glm"
            assert engines.calls[1][0] == "engine-finished" and engines.calls[2][1] == "maya_glm"
    finally:
        finish.set()
        server.should_exit = True
        await asyncio.to_thread(thread.join, 5)
        sock.close()


@pytest.mark.asyncio
async def test_live_retention_reload_changes_expiry_without_restart(tmp_path):
    c, engines, now = fixture(tmp_path)
    async with c.lease("ds", "one"):
        pass
    updated = {alias: {**route, "keep_alive_seconds": 0} for alias, route in c.routes.items()}
    app = create_app(c, reload_routes=lambda: updated)
    async with app.router.lifespan_context(app):
        await asyncio.sleep(1.1)
    assert c.resident is None
    assert engines.calls[-1] == ("release", "maya", "deepseek-v4.1-flash")
