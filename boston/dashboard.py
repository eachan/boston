from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from boston.config import AppConfig
from boston.hardware import get_audio_input_status, get_audio_output_status, get_hailo_status, get_system_stats
from boston.storage import Storage
from boston.system_control import SystemController


LOGGER = logging.getLogger(__name__)


def create_dashboard_app(config: AppConfig, storage: Storage) -> FastAPI:
    app = FastAPI(title="Boston Dashboard", version="1.0.0")
    system = SystemController()

    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    static_dir = Path(__file__).resolve().parent.parent / "static"
    templates = Jinja2Templates(directory=str(templates_dir))
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    def build_status() -> dict:
        runtime = storage.get_runtime_state()
        runtime_services = runtime.get("services", {}) if isinstance(runtime, dict) else {}
        services_status = {
            name: system.service_status(name) for name in config.services.managed_services
        }
        return {
            "runtime": runtime,
            "services": services_status,
            "hardware": {
                "hailo": get_hailo_status(config.model.hailo_status_command),
                "audio_input": get_audio_input_status(),
                "audio_output": get_audio_output_status(),
            },
            "system": get_system_stats(),
            "runtime_services": runtime_services,
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard_page(request: Request) -> HTMLResponse:
        status = build_status()
        events = storage.get_recent_events(60)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "status": status,
                "events": events,
                "refresh_seconds": config.dashboard.refresh_seconds,
                "page": "dashboard",
            },
        )

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request) -> HTMLResponse:
        matches = storage.get_match_history(100)
        return templates.TemplateResponse(
            "history.html",
            {"request": request, "matches": matches, "page": "history"},
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        services_status = {
            name: system.service_status(name) for name in config.services.managed_services
        }
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "services": services_status,
                "managed_services": config.services.managed_services,
                "page": "settings",
            },
        )

    @app.get("/api/status")
    async def api_status() -> JSONResponse:
        return JSONResponse(build_status())

    @app.post("/settings/wifi")
    async def add_wifi(ssid: str = Form(...), password: str = Form(...)) -> RedirectResponse:
        result = system.add_wifi_network(ssid, password)
        storage.add_event(
            "settings_wifi",
            detail=result.message,
            payload={"ssid": ssid, "ok": result.ok, "command": result.command},
        )
        return RedirectResponse(url="/settings", status_code=303)

    @app.post("/action/service")
    async def service_action(service: str = Form(...), action: str = Form(...)) -> RedirectResponse:
        if action == "start":
            result = system.service_start(service)
        elif action == "stop":
            result = system.service_stop(service)
        else:
            result = system.service_restart(service)
        storage.add_event(
            "settings_service_action",
            detail=result.message,
            payload={"service": service, "action": action, "ok": result.ok, "command": result.command},
        )
        return RedirectResponse(url="/settings", status_code=303)

    @app.post("/action/system")
    async def system_action(action: str = Form(...)) -> RedirectResponse:
        if action == "reboot":
            result = system.reboot()
        else:
            result = system.shutdown()
        storage.add_event(
            "settings_system_action",
            detail=result.message,
            payload={"action": action, "ok": result.ok, "command": result.command},
        )
        return RedirectResponse(url="/settings", status_code=303)

    return app
