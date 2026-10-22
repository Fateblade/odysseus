"""Admin-only Operations API; host targets are deployment-configured."""

import json
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from core.middleware import require_admin
from services.operations.litellm import HostOperations
from services.operations.litellm_config import MAX_CONFIG_BYTES, OperationsError


def setup_operations_routes(service=None):
    router = APIRouter(prefix="/api/operations", tags=["operations"])
    operations = service or HostOperations()

    async def invoke(request, action):
        require_admin(request)
        values = {}
        if request.method == "POST":
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.url.netloc:
                raise HTTPException(403, "Cross-origin management requests are not allowed")
            if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
                raise HTTPException(415, "Use application/json")
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > MAX_CONFIG_BYTES:
                    raise HTTPException(413, "Operation request is too large")
            try:
                values = json.loads(raw)
                if not isinstance(values, dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(400, "Operation request must be a JSON object") from None
        try:
            return await operations.run(action, values)
        except OperationsError as error:
            raise HTTPException(error.status, str(error)) from None

    @router.get("/overview")
    async def get_host_overview(request: Request):
        return await invoke(request, "overview")

    @router.get("/litellm")
    async def get_configuration(request: Request):
        return await invoke(request, "snapshot")

    @router.get("/management")
    async def get_management(request: Request):
        return await invoke(request, "management")

    @router.get("/residency")
    async def get_residency(request: Request):
        return await invoke(request, "residency")

    @router.post("/manage")
    async def manage_host(request: Request):
        return await invoke(request, "manage")

    @router.get("/jobs")
    async def get_jobs(request: Request):
        return await invoke(request, "jobs")

    @router.post("/jobs/detail")
    async def get_job(request: Request):
        return await invoke(request, "job_detail")

    @router.post("/jobs/start")
    async def start_job(request: Request):
        return await invoke(request, "job_start")

    @router.post("/jobs/cancel")
    async def cancel_job(request: Request):
        return await invoke(request, "job_cancel")

    @router.post("/scripts/save")
    async def save_script(request: Request):
        return await invoke(request, "script_save")

    @router.get("/strata/config")
    async def get_strata_config(request: Request):
        return await invoke(request, "strata_config")

    @router.post("/strata/preview")
    async def preview_strata_config(request: Request):
        return await invoke(request, "strata_preview")

    @router.post("/strata/apply")
    async def apply_strata_config(request: Request):
        return await invoke(request, "strata_apply")

    @router.get("/dwarfstar/config")
    async def get_dwarfstar_config(request: Request):
        return await invoke(request, "dwarfstar_config")

    @router.post("/dwarfstar/preview")
    async def preview_dwarfstar_config(request: Request):
        return await invoke(request, "dwarfstar_preview")

    @router.post("/dwarfstar/apply")
    async def apply_dwarfstar_config(request: Request):
        return await invoke(request, "dwarfstar_apply")

    @router.post("/litellm/preview")
    async def preview_configuration(request: Request):
        return await invoke(request, "preview")

    @router.post("/litellm/probe")
    async def probe_deployment(request: Request):
        return await invoke(request, "probe")

    @router.post("/litellm/apply")
    async def apply_configuration(request: Request):
        return await invoke(request, "apply")

    return router
