"""Admin-only Operations API; host targets are deployment-configured."""

import json
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

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
            values["_audit"] = {"actor": getattr(request.state, "current_user", None) or "auth-disabled or internal admin",
                                "client_ip": request.client.host if request.client else None,
                                "user_agent": request.headers.get("user-agent", "")[:512], "request_id": uuid.uuid4().hex}
            result = await operations.run(action, values)
            if action in ("audit", "audit_detail"):
                return JSONResponse(result, headers={"Cache-Control": "no-store"})
            return result
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

    @router.get("/audit")
    async def get_audit(request: Request):
        return await invoke(request, "audit")

    @router.post("/audit/detail")
    async def get_audit_detail(request: Request):
        return await invoke(request, "audit_detail")

    @router.post("/manage")
    async def manage_host(request: Request):
        return await invoke(request, "manage")

    @router.get("/jobs")
    async def get_jobs(request: Request):
        return await invoke(request, "jobs")

    @router.post("/measurements")
    async def get_measurements(request: Request):
        return await invoke(request, "measurements")

    @router.post("/measurements/import")
    async def import_measurements(request: Request):
        return await invoke(request, "measurement_import")

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

    @router.get("/maya/config")
    async def get_maya_config(request: Request):
        return await invoke(request, "maya_config")

    @router.post("/maya/preview")
    async def preview_maya_config(request: Request):
        return await invoke(request, "maya_preview")

    @router.post("/maya/apply")
    async def apply_maya_config(request: Request):
        return await invoke(request, "maya_apply")

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
