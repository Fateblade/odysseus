"""Local model-aware proxy with serialized leases and completion-based idle expiry."""

import asyncio
import hmac
import json
import math
import os
import time
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from services.operations.audit import directory, emit
from services.operations.inference_backends import NativeEngines
from services.operations.inference_lifecycle import runtime_lease
from services.operations.litellm_config import OperationsError

BACKENDS = {"strata", "ollama", "dwarfstar", "maya", "maya_glm"}
PATHS = {"/v1/chat/completions", "/v1/completions", "/v1/embeddings", "/v1/responses", "/api/chat", "/api/generate", "/api/embed", "/api/embeddings"}
MAX_BODY = 16 * 1024 * 1024


def validate_routes(value, management):
    if not isinstance(value, dict) or not value:
        raise ValueError("Configure at least one explicit inference route.")
    result = {}
    for alias, route in value.items():
        if not isinstance(alias, str) or not alias.strip() or not isinstance(route, dict):
            raise ValueError("Routes need a public name and an object definition.")
        backend, model = route.get("backend"), route.get("model")
        ttl = route.get("keep_alive_seconds", 600)
        if backend not in BACKENDS or not isinstance(model, str) or not model.strip():
            raise ValueError("Each route needs an allowlisted backend and native model name.")
        if type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl < 0:
            raise ValueError("Keep-alive must be a finite, nonnegative number of seconds.")
        url = getattr(management, backend + "_url", "")
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Managed backend endpoints must be fixed loopback HTTP origins.")
        if backend in ("maya", "maya_glm", "dwarfstar") and backend not in management.settings.get("services", {}):
            raise ValueError("Exclusive engine routes require an allowlisted systemd service.")
        result[alias] = {"backend": backend, "model": model, "keep_alive_seconds": float(ttl), "url": url.rstrip("/")}
    return result


async def finish_thread(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A cancelled HTTP request must not release a lease while systemctl still runs.
        await task
        raise


class Coordinator:
    def __init__(self, management, routes, engines=None, clock=time.monotonic):
        self.management = management
        self.routes = validate_routes(routes, management)
        self.engines = engines or NativeEngines(management)
        self.clock = clock
        self.lock = asyncio.Lock()
        self.active = None
        self.queued = 0
        self.resident = None
        self.last_finished = None
        self.last_error = None
        self.retry_after = 0
        self.uncertain = False
        self.state_path = Path(management.path).parent / "inference-router-state.json" if management.path else None
        if self.state_path and self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text(encoding="utf-8"))
                alias = state.get("resident")
                if state.get("active") or state.get("uncertain"):
                    self.uncertain = True
                    self.last_error = "The previous router stopped during a request. Inspect the engines before clearing inference-router-state.json and restarting."
                elif alias in self.routes:
                    self.resident = alias
                    elapsed = max(0, time.time() - float(state["finished_at"]))
                    self.last_finished = self.clock() - elapsed
            except (OSError, ValueError, TypeError, KeyError):
                raise ValueError("Invalid saved router state; inspect inference-router-state.json before starting.") from None

    def persist(self, active=False):
        if self.state_path:
            from services.operations.jobs import write_json
            write_json(self.state_path, {"resident": self.resident, "active": active,
                                         "finished_at": time.time(), "uncertain": self.uncertain})

    def log(self, event, route, **fields):
        emit(directory(self.management), event, source="inference-router", backend=route["backend"], model=route["model"], **fields)

    @asynccontextmanager
    async def lease(self, alias, request_id, caller=None):
        route = self.routes[alias]
        self.queued += 1
        try:
            try:
                await asyncio.wait_for(self.lock.acquire(), timeout=900)
            except TimeoutError:
                raise OperationsError("Inference routing queue timed out.", 504) from None
        finally:
            self.queued -= 1
        try:
            with runtime_lease(self.management):
                if self.uncertain:
                    raise OperationsError("A previous upstream request ended unexpectedly. Inspect the engine and restart the router before switching models.", 409)
                self.active = alias
                self.management.routing_actor = {"request_id": request_id, **(caller or {})}
                self.last_error = None
                prepared = False
                try:
                    self.persist(active=True)
                    self.log("routing.request_started", route, request_id=request_id, requested_model=alias, **(caller or {}))
                    self.log("routing.load_requested", route, request_id=request_id)
                    await finish_thread(self.engines.prepare, route["backend"], route["model"])
                    self.resident = alias
                    prepared = True
                    yield route
                except BaseException as error:
                    self.last_error = type(error).__name__
                    self.log("routing.request_failed", route, request_id=request_id, error_type=type(error).__name__)
                    # Partial starts also need idle cleanup, without claiming readiness.
                    self.resident = alias
                    raise
                finally:
                    try:
                        if prepared and route["backend"] == "ollama" and hasattr(self.engines, "pin"):
                            await finish_thread(self.engines.pin, route["backend"], route["model"])
                    except Exception as error:
                        self.last_error = type(error).__name__
                        self.log("routing.keep_alive_failed", route, error_type=type(error).__name__)
                    finally:
                        self.last_finished = self.clock()
                        self.active = None
                        self.persist()
                        self.log("routing.request_finished", route, request_id=request_id)
        finally:
            self.lock.release()

    async def expire(self):
        if self.uncertain or self.lock.locked() or self.queued or self.resident is None or self.last_finished is None:
            return
        async with self.lock:
            alias = self.resident
            route = self.routes[alias]
            if self.clock() < self.retry_after or self.clock() - self.last_finished < route["keep_alive_seconds"]:
                return
            try:
                with runtime_lease(self.management):
                    self.log("routing.idle_unload_requested", route)
                    await finish_thread(self.engines.release, route["backend"], route["model"])
                    self.resident = None
                    self.persist()
                    self.last_error = None
                    self.log("routing.idle_unloaded", route)
            except OperationsError as error:
                self.last_error = str(error)
                self.retry_after = self.clock() + 30
                self.log("routing.idle_unload_failed", route, error_type=type(error).__name__)

    def quarantine(self, error):
        self.uncertain = True
        self.last_error = "Upstream completion was not confirmed: " + type(error).__name__
        self.persist(active=self.active is not None)
        alias = self.active or self.resident
        if alias in self.routes:
            self.log("routing.completion_unconfirmed", self.routes[alias], error_type=type(error).__name__)

    def status(self):
        return {"status": "blocked" if self.uncertain else "ok" if not self.last_error else "degraded", "active": self.active, "queued": self.queued,
                "resident_route": self.resident, "last_error": self.last_error,
                "idle_seconds": None if self.last_finished is None else max(0, self.clock() - self.last_finished),
                "routes": {alias: {key: route[key] for key in ("backend", "model", "keep_alive_seconds")} for alias, route in self.routes.items()}}


class LeasedResponse(StreamingResponse):
    def __init__(self, upstream, client, lease, coordinator=None):
        self.coordinator = coordinator
        self.upstream, self.client, self.lease = upstream, client, lease
        headers = {name: upstream.headers[name] for name in ("content-type", "content-encoding", "x-request-id") if name in upstream.headers}
        headers["Cache-Control"] = "no-store"
        super().__init__(upstream.aiter_raw(), status_code=upstream.status_code, headers=headers)

    async def __call__(self, scope, receive, send):
        disconnected = False

        async def watch_disconnect():
            nonlocal disconnected
            while True:
                if (await receive())["type"] == "http.disconnect":
                    disconnected = True
                    return

        async def forward():
            nonlocal disconnected
            try:
                await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            except OSError:
                disconnected = True
            async for chunk in self.body_iterator:
                if not disconnected:
                    try:
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    except OSError:
                        disconnected = True
            if not disconnected:
                await send({"type": "http.response.body", "body": b"", "more_body": False})

        reader = asyncio.create_task(forward())
        watcher = asyncio.create_task(watch_disconnect())
        try:
            try:
                await asyncio.shield(reader)
            except asyncio.CancelledError:
                disconnected = True
                # Drain the engine even when the caller leaves; another route cannot evict it mid-generation.
                await asyncio.shield(reader)
                raise
        except Exception as error:
            if self.coordinator:
                self.coordinator.quarantine(error)
            raise
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            try:
                await self.upstream.aclose()
            finally:
                try:
                    await self.client.aclose()
                finally:
                    await self.lease.__aexit__(None, None, None)


def create_app(coordinator, client_factory=None, reload_routes=None):
    key = os.environ.get("ODYSSEUS_INFERENCE_ROUTER_KEY", "")

    @asynccontextmanager
    async def lifespan(app):
        async def reaper():
            while True:
                await asyncio.sleep(1)
                try:
                    if reload_routes:
                        routes = validate_routes(await finish_thread(reload_routes), coordinator.management)
                        if set(routes) != set(coordinator.routes) or any(routes[name]["backend"] != old["backend"] or routes[name]["model"] != old["model"] for name, old in coordinator.routes.items()):
                            raise ValueError("Restart the router after changing route identities.")
                        for name in routes:
                            coordinator.routes[name]["keep_alive_seconds"] = routes[name]["keep_alive_seconds"]
                    await coordinator.expire()
                except Exception as error:
                    coordinator.last_error = type(error).__name__
        worker = asyncio.create_task(reaper())
        try:
            yield
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def endpoint(request: Request):
        if key and not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + key):
            return JSONResponse({"error": {"message": "Invalid local router credentials."}}, status_code=401)
        if request.method == "GET":
            if request.url.path in ("/health", "/status"):
                return JSONResponse(coordinator.status(), headers={"Cache-Control": "no-store"})
            return JSONResponse({"object": "list", "data": [{"id": alias, "object": "model", "owned_by": "odysseus"} for alias in coordinator.routes]})
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                return JSONResponse({"error": {"message": "Inference request exceeds 16 MiB."}}, status_code=413)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or not isinstance(payload.get("model"), str) or payload["model"] not in coordinator.routes:
                raise ValueError()
            alias = payload["model"]
            if request.url.path.startswith("/api/") and coordinator.routes[alias]["backend"] != "ollama":
                return JSONResponse({"error": {"message": "Native /api endpoints require an Ollama route."}}, status_code=400)
        except (ValueError, UnicodeError):
            return JSONResponse({"error": {"message": "Supply a configured router model name in a JSON object."}}, status_code=400)
        request_id = uuid.uuid4().hex
        caller = {"actor": "local inference client", "client_ip": request.client.host if request.client else None,
                  "user_agent": request.headers.get("user-agent", "")[:512], "client_label": request.headers.get("x-client-name", "")[:128]}
        lease = coordinator.lease(alias, request_id, caller)
        client = upstream = None
        entered = False
        try:
            route = await lease.__aenter__()
            entered = True
            payload["model"] = route["model"]
            payload.pop("keep_alive", None)
            if route["backend"] == "ollama" and request.url.path.startswith("/api/"):
                payload["keep_alive"] = -1
            client = client_factory() if client_factory else httpx.AsyncClient(timeout=httpx.Timeout(3600, connect=10), trust_env=False, follow_redirects=False)
            headers = {name: request.headers[name] for name in ("user-agent", "x-client-name") if name in request.headers}
            headers["x-request-id"] = request_id
            upstream = await client.send(client.build_request("POST", route["url"] + request.url.path, json=payload, headers=headers), stream=True)
            coordinator.log("routing.upstream_response", route, request_id=request_id, status_code=upstream.status_code)
            return LeasedResponse(upstream, client, lease, coordinator)
        except BaseException as error:
            if entered and client is not None and not isinstance(error, httpx.ConnectError):
                coordinator.quarantine(error)
            if upstream is not None:
                await upstream.aclose()
            if client is not None:
                await client.aclose()
            if entered:
                await lease.__aexit__(type(error), error, error.__traceback__)
            if isinstance(error, asyncio.CancelledError):
                raise
            code = error.status if isinstance(error, OperationsError) else 502
            return JSONResponse({"error": {"message": str(error) if isinstance(error, OperationsError) else "Inference routing failed; inspect the request journal.", "request_id": request_id}}, status_code=code)

    paths = [Route(path, endpoint, methods=["POST"]) for path in sorted(PATHS)]
    paths += [Route(path, endpoint, methods=["GET"]) for path in ("/health", "/status", "/v1/models")]
    return Starlette(routes=paths, lifespan=lifespan)
