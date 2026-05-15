from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.app.camera.rtsp import mask_rtsp_url
from backend.app.config import load_settings
from backend.app.db import Database, Repository
from backend.app.domain import (
    CameraConfig,
    FailPolicy,
    RelayAction,
    RelayDesiredState,
    RuleCondition,
    RuleConfig,
    SnapshotDelivery,
    WebhookAction,
)
from backend.app.plugins.object_count import ObjectCountPlugin


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
settings = load_settings()
repo = Repository(Database(settings.db_path))

app = FastAPI(title="Smart AI Access Control")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    public = path.startswith("/static") or path in {"/login", "/bootstrap"}
    if public:
        return await call_next(request)
    if not repo.is_bootstrapped():
        return RedirectResponse("/bootstrap", status_code=303)
    if repo.has_session(request.cookies.get("smartai_session")):
        return await call_next(request)
    if path.startswith("/api"):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def wants_fragment(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


@app.get("/", response_class=HTMLResponse)
def index() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/bootstrap", response_class=HTMLResponse)
def bootstrap_page(request: Request) -> Response:
    if repo.is_bootstrapped():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "pages/bootstrap.html", {})


@app.post("/bootstrap", response_class=HTMLResponse)
def bootstrap_admin(password: str = Form(...)) -> Response:
    if len(password) < 12:
        raise HTTPException(status_code=400, detail="password must be at least 12 characters")
    repo.bootstrap_admin(password)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    if not repo.is_bootstrapped():
        return RedirectResponse("/bootstrap", status_code=303)
    return templates.TemplateResponse(request, "pages/login.html", {})


@app.post("/login", response_class=HTMLResponse)
def login(password: str = Form(...)) -> Response:
    if not repo.verify_admin(password):
        raise HTTPException(status_code=401, detail="invalid password")
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("smartai_session", repo.create_session(), httponly=True, samesite="strict")
    return response


@app.post("/logout")
def logout(request: Request) -> Response:
    repo.delete_session(request.cookies.get("smartai_session"))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("smartai_session")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    cameras = repo.list_cameras()
    rules = repo.list_rules()
    relays = repo.list_relays()
    events = repo.list_events(limit=10)
    return templates.TemplateResponse(
        request,
        "pages/dashboard.html",
        {
            "camera_count": len(cameras),
            "rule_count": len(rules),
            "relay_count": len(relays),
            "event_count": len(events),
            "plugins": [ObjectCountPlugin.name],
            "events": events,
        },
    )


@app.get("/cameras", response_class=HTMLResponse)
def cameras_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/cameras.html", {"cameras": repo.list_cameras()})


@app.get("/ui/cameras/table", response_class=HTMLResponse)
def cameras_table(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/cameras_table.html", {"cameras": repo.list_cameras()})


@app.post("/ui/cameras", response_class=HTMLResponse)
def create_camera(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(554),
    username: str = Form(""),
    password: str = Form(""),
    path: str = Form(...),
    transport: str = Form("tcp"),
    analytics_fps: float = Form(2.0),
) -> Response:
    if analytics_fps <= 0 or analytics_fps > 5:
        raise HTTPException(status_code=400, detail="analytics_fps must be between 0 and 5")
    camera = CameraConfig(
        id=new_id("cam"),
        name=name,
        host=host,
        port=port,
        username=username or None,
        password=password or None,
        path=path,
        transport=transport,
        analytics_fps=analytics_fps,
    )
    repo.save_camera(camera)
    if wants_fragment(request):
        return cameras_table(request)
    return RedirectResponse("/cameras", status_code=303)


@app.post("/ui/cameras/{camera_id}/delete", response_class=HTMLResponse)
def delete_camera(request: Request, camera_id: str) -> Response:
    repo.delete_camera(camera_id)
    if wants_fragment(request):
        return cameras_table(request)
    return RedirectResponse("/cameras", status_code=303)


@app.post("/ui/cameras/{camera_id}/test", response_class=HTMLResponse)
def test_camera(request: Request, camera_id: str) -> HTMLResponse:
    camera = repo.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404)
    repo.set_camera_health(camera_id, "configured")
    return templates.TemplateResponse(
        request,
        "partials/camera_test_result.html",
        {"camera": camera, "masked_url": mask_rtsp_url(camera)},
    )


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/rules.html",
        {"rules": repo.list_rules(), "cameras": repo.list_cameras(), "relays": repo.list_relays()},
    )


@app.get("/ui/rules/table", response_class=HTMLResponse)
def rules_table(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/rules_table.html", {"rules": repo.list_rules()})


@app.post("/ui/rules", response_class=HTMLResponse)
def create_rule(
    request: Request,
    name: str = Form(...),
    camera_id: str = Form(...),
    metric: str = Form("person.count"),
    operator: str = Form(">="),
    value: int = Form(2),
    true_relay_id: str = Form(""),
    true_relay_state: RelayDesiredState = Form(RelayDesiredState.ON),
    false_relay_id: str = Form(""),
    false_relay_state: RelayDesiredState = Form(RelayDesiredState.OFF),
    webhook_url: str = Form(""),
    webhook_snapshot_delivery: SnapshotDelivery = Form(SnapshotDelivery.MULTIPART),
    debounce_true_ms: int = Form(500),
    debounce_false_ms: int = Form(500),
    cooldown_ms: int = Form(1000),
    fail_policy: FailPolicy = Form(FailPolicy.GLOBAL),
) -> Response:
    if repo.get_camera(camera_id) is None:
        raise HTTPException(status_code=400, detail="unknown camera")

    true_actions = []
    false_actions = []
    fault_actions = []
    if true_relay_id:
        true_actions.append(RelayAction(true_relay_id, true_relay_state))
    if false_relay_id:
        false_actions.append(RelayAction(false_relay_id, false_relay_state))
    if webhook_url:
        true_actions.append(WebhookAction(webhook_url, webhook_snapshot_delivery))
    if fail_policy == FailPolicy.FAIL_OFF and true_relay_id:
        fault_actions.append(RelayAction(true_relay_id, RelayDesiredState.OFF))
    elif fail_policy == FailPolicy.FAIL_ON and true_relay_id:
        fault_actions.append(RelayAction(true_relay_id, RelayDesiredState.ON))

    rule = RuleConfig(
        id=new_id("rule"),
        name=name,
        enabled=True,
        priority=100,
        camera_ids=[camera_id],
        plugin_id="core.object_count",
        condition=RuleCondition(metric=metric, operator=operator, value=value),
        true_actions=true_actions,
        false_actions=false_actions,
        fault_actions=fault_actions,
        debounce_true_ms=debounce_true_ms,
        debounce_false_ms=debounce_false_ms,
        cooldown_ms=cooldown_ms,
        fail_policy=fail_policy,
    )
    repo.save_rule(rule)
    if wants_fragment(request):
        return rules_table(request)
    return RedirectResponse("/rules", status_code=303)


@app.post("/ui/rules/{rule_id}/delete", response_class=HTMLResponse)
def delete_rule(request: Request, rule_id: str) -> Response:
    repo.delete_rule(rule_id)
    if wants_fragment(request):
        return rules_table(request)
    return RedirectResponse("/rules", status_code=303)


@app.get("/relays", response_class=HTMLResponse)
def relays_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/relays.html", {"relays": repo.list_relays()})


@app.post("/ui/relays/test-channel", response_class=HTMLResponse)
def test_relay(
    request: Request,
    relay_id: str = Form(...),
    state_value: RelayDesiredState = Form(...),
) -> HTMLResponse:
    relay = repo.get_relay(relay_id)
    if relay is None:
        raise HTTPException(status_code=404)
    repo.update_relay_state(relay_id, state_value)
    return templates.TemplateResponse(
        request,
        "partials/relay_test_result.html",
        {"relay": relay, "state_value": state_value, "hardware_enabled": settings.enable_relay_hardware},
    )


@app.get("/events", response_class=HTMLResponse)
def events_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/events.html", {"events": repo.list_events(limit=250)})


@app.get("/api/events/{event_id}/snapshot")
def event_snapshot(event_id: str) -> FileResponse:
    event = repo.get_event(event_id)
    if event is None or not event["snapshot_path"]:
        raise HTTPException(status_code=404)
    path = Path(event["snapshot_path"])
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@app.get("/storage", response_class=HTMLResponse)
def storage_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/storage.html", {"settings": repo.settings_dict()})


@app.post("/ui/storage", response_class=HTMLResponse)
def update_storage(
    request: Request,
    snapshot_max_bytes: int = Form(...),
    snapshot_min_free_disk_percent: int = Form(...),
    webhook_url: str = Form(""),
    webhook_snapshot_delivery: SnapshotDelivery = Form(SnapshotDelivery.MULTIPART),
    webhook_hmac_secret: str = Form(""),
) -> Response:
    repo.set_setting("snapshot_max_bytes", str(snapshot_max_bytes))
    repo.set_setting("snapshot_min_free_disk_percent", str(snapshot_min_free_disk_percent))
    repo.set_setting("webhook_url", webhook_url)
    repo.set_setting("webhook_snapshot_delivery", webhook_snapshot_delivery.value)
    if webhook_hmac_secret:
        repo.set_setting("webhook_hmac_secret", webhook_hmac_secret)
    if wants_fragment(request):
        return templates.TemplateResponse(request, "partials/storage_settings.html", {"settings": repo.settings_dict()})
    return RedirectResponse("/storage", status_code=303)


@app.get("/api/system/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "cameras": len(repo.list_cameras()),
        "rules": len(repo.list_rules()),
        "relays": len(repo.list_relays()),
        "events": len(repo.list_events(limit=1000)),
        "mockInference": settings.use_mock_inference,
        "relayHardware": settings.enable_relay_hardware,
        "db": str(settings.db_path),
        "pid": os.getpid(),
    }


@app.get("/ui/system/health", response_class=HTMLResponse)
def health_panel(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/health.html", {"health": health()})
