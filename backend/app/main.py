from __future__ import annotations

import os
import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.app.camera.rtsp import mask_rtsp_url
from backend.app.camera.snapshot import capture_rtsp_jpeg_result
from backend.app.config import load_settings
from backend.app.db import Database, Repository
from backend.app.domain import (
    CameraConfig,
    ConditionGroup,
    FailPolicy,
    MonitorConfig,
    RelayAction,
    RelayDesiredState,
    RuleCondition,
    RuleConfig,
    SnapshotDelivery,
    WebhookAction,
)
from backend.app.models import MODEL_DEFINITIONS, model_options, monitor_templates
from backend.app.plugins.object_count import ObjectCountPlugin
from backend.app.system_metrics import performance_snapshot


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
settings = load_settings()
repo = Repository(Database(settings.db_path))
LOCAL_TIMEZONE = ZoneInfo(os.environ.get("SMARTAI_TIMEZONE", "Africa/Johannesburg"))

app = FastAPI(title="Smart AI Access Control")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    public = path.startswith("/static") or path in {"/login", "/bootstrap", "/healthz"}
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
    cameras_by_id = {camera.id: camera for camera in cameras}
    rules_by_id = {rule.id: rule for rule in rules}
    events = event_view_rows(repo.list_events(limit=10), cameras_by_id, rules_by_id)
    return templates.TemplateResponse(
        request,
        "pages/dashboard.html",
        {
            "camera_count": len(cameras),
            "monitor_count": len(repo.list_monitors()),
            "rule_count": len(rules),
            "relay_count": len(relays),
            "event_count": len(events),
            "plugins": [ObjectCountPlugin.name],
            "events": events,
        },
    )


@app.get("/monitors", response_class=HTMLResponse)
def monitors_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/monitors.html",
        {
            "monitors": repo.list_monitors(),
            "cameras": repo.list_cameras(),
            "models": model_options(),
            "templates": monitor_templates(),
            "monitor_states": repo.list_monitor_states(),
            "monitor_debug_snapshots": repo.list_monitor_debug_snapshots(),
            "monitor_runtime_rows": repo.list_monitor_runtime_rows(),
            "inference_mode": "mock" if settings.use_mock_inference else "real",
        },
    )


@app.get("/ui/monitors/table", response_class=HTMLResponse)
def monitors_table(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/monitors_table.html",
        {
            "monitors": repo.list_monitors(),
            "monitor_states": repo.list_monitor_states(),
            "monitor_debug_snapshots": repo.list_monitor_debug_snapshots(),
            "monitor_runtime_rows": repo.list_monitor_runtime_rows(),
        },
    )


@app.get("/ui/monitors/outputs", response_class=HTMLResponse)
def monitor_outputs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/monitor_outputs.html",
        {
            "monitor_states": repo.list_monitor_states(),
        },
    )


@app.get("/ui/monitors/debug-snapshots", response_class=HTMLResponse)
def monitor_debug_snapshots(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/monitor_debug_snapshots.html",
        {
            "monitor_debug_snapshots": repo.list_monitor_debug_snapshots(),
        },
    )


@app.get("/ui/monitors/{monitor_id}/edit", response_class=HTMLResponse)
def edit_monitor_form(request: Request, monitor_id: str) -> HTMLResponse:
    monitor = repo.get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "partials/monitor_edit_form.html",
        {
            "monitor": monitor,
            "cameras": repo.list_cameras(),
            "models": model_options(),
            "templates": monitor_templates(),
            "debug_snapshot": repo.get_monitor_debug_snapshot(monitor_id),
        },
    )


@app.post("/ui/monitors", response_class=HTMLResponse)
def create_monitor(
    request: Request,
    name: str = Form(...),
    model_id: str = Form("object_detector"),
    camera_id: str = Form(...),
    class_name: str = Form("person"),
    confidence_threshold: float = Form(0.5),
) -> Response:
    if model_id not in MODEL_DEFINITIONS:
        raise HTTPException(status_code=400, detail="unknown model")
    if repo.get_camera(camera_id) is None:
        raise HTTPException(status_code=400, detail="unknown camera")
    monitor = MonitorConfig(
        id=new_id("mon"),
        name=name,
        model_id=model_id,
        camera_id=camera_id,
        config={"class_name": class_name, "confidence_threshold": confidence_threshold},
    )
    repo.save_monitor(monitor)
    if wants_fragment(request):
        return monitors_table(request)
    return RedirectResponse("/monitors", status_code=303)


@app.post("/ui/monitors/{monitor_id}", response_class=HTMLResponse)
def update_monitor(
    request: Request,
    monitor_id: str,
    name: str = Form(...),
    model_id: str = Form("object_detector"),
    camera_id: str = Form(...),
    class_name: str = Form("person"),
    confidence_threshold: float = Form(0.5),
    zone_enabled: str | None = Form(None),
    zone_id: str = Form("zone-1"),
    zone_x: float = Form(0),
    zone_y: float = Form(0),
    zone_width: float = Form(0),
    zone_height: float = Form(0),
) -> Response:
    existing = repo.get_monitor(monitor_id)
    if existing is None:
        raise HTTPException(status_code=404)
    if model_id not in MODEL_DEFINITIONS:
        raise HTTPException(status_code=400, detail="unknown model")
    if repo.get_camera(camera_id) is None:
        raise HTTPException(status_code=400, detail="unknown camera")
    config: dict[str, object] = {"class_name": class_name, "confidence_threshold": confidence_threshold}
    if zone_enabled and zone_width > 0 and zone_height > 0:
        config["zone_id"] = zone_id or "zone-1"
        config["zone"] = normalized_zone(zone_x, zone_y, zone_width, zone_height)
    repo.save_monitor(
        MonitorConfig(
            id=monitor_id,
            name=name,
            model_id=model_id,
            camera_id=camera_id,
            enabled=existing.enabled,
            config=config,
        )
    )
    if wants_fragment(request):
        return monitors_table(request)
    return RedirectResponse("/monitors", status_code=303)


@app.post("/ui/monitors/{monitor_id}/delete", response_class=HTMLResponse)
def delete_monitor(request: Request, monitor_id: str) -> Response:
    repo.delete_monitor(monitor_id)
    if wants_fragment(request):
        return monitors_table(request)
    return RedirectResponse("/monitors", status_code=303)


@app.get("/api/monitors/{monitor_id}/debug-snapshot")
def monitor_debug_snapshot(monitor_id: str) -> FileResponse:
    row = repo.get_monitor_debug_snapshot(monitor_id)
    if row is None:
        raise HTTPException(status_code=404)
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type=row["mime_type"], headers={"Content-Disposition": "inline"})


@app.get("/monitors/{monitor_id}/debug-snapshot", response_class=HTMLResponse)
def monitor_debug_snapshot_page(request: Request, monitor_id: str) -> HTMLResponse:
    row = repo.get_monitor_debug_snapshot(monitor_id)
    if row is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "pages/snapshot.html",
        {
            "title": "Monitor Debug Snapshot",
            "subtitle": row["observed_at"],
            "image_url": f"/api/monitors/{monitor_id}/debug-snapshot",
        },
    )


@app.get("/cameras", response_class=HTMLResponse)
def cameras_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "pages/cameras.html", {"cameras": repo.list_camera_rows()})


@app.get("/ui/cameras/table", response_class=HTMLResponse)
def cameras_table(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/cameras_table.html", {"cameras": repo.list_camera_rows()})


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


@app.post("/ui/cameras/{camera_id}/snapshot", response_class=HTMLResponse)
def test_camera_snapshot(request: Request, camera_id: str) -> HTMLResponse:
    camera = repo.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404)
    result = capture_rtsp_jpeg_result(camera)
    return templates.TemplateResponse(
        request,
        "partials/camera_snapshot_result.html",
        {
            "camera": camera,
            "ok": result.ok,
            "error": result.error,
            "bytes_len": len(result.jpeg_bytes) if result.jpeg_bytes else 0,
        },
    )


@app.get("/api/cameras/{camera_id}/snapshot")
def camera_snapshot(camera_id: str) -> Response:
    camera = repo.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404)
    result = capture_rtsp_jpeg_result(camera)
    if result.jpeg_bytes is None:
        raise HTTPException(status_code=502, detail=result.error or "snapshot capture failed")
    return Response(content=result.jpeg_bytes, media_type="image/jpeg", headers={"Content-Disposition": "inline"})


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/rules.html",
        rule_template_context(request, action_url="/ui/rules", submit_label="Add Rule"),
    )


@app.get("/ui/rules/table", response_class=HTMLResponse)
def rules_table(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/rules_table.html", rules_table_context())


@app.get("/ui/rules/{rule_id}/edit", response_class=HTMLResponse)
def edit_rule_form(request: Request, rule_id: str) -> HTMLResponse:
    rule = repo.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "partials/rule_form.html",
        rule_template_context(
            request,
            rule=rule,
            action_url=f"/ui/rules/{rule.id}",
            submit_label="Save Rule",
        ),
    )


def rules_table_context() -> dict[str, object]:
    monitors = repo.list_monitors()
    return {
        "rules": repo.list_rules(),
        "monitors_by_id": {monitor.id: monitor for monitor in monitors},
    }


def rule_template_context(
    request: Request,
    *,
    action_url: str,
    submit_label: str,
    rule: RuleConfig | None = None,
) -> dict[str, object]:
    monitors = repo.list_monitors()
    return {
        "request": request,
        **rules_table_context(),
        "rule": rule,
        "monitors": monitors,
        "metric_options": monitor_metric_options(monitors),
        "relays": repo.list_relays(),
        "action_url": action_url,
        "submit_label": submit_label,
        "form": rule_form_values(rule),
    }


def monitor_metric_options(monitors: list[MonitorConfig]) -> list[dict[str, str]]:
    options = []
    for monitor in monitors:
        class_name = str(monitor.config.get("class_name", "person"))
        label = class_name.title()
        options.append({"monitor_id": monitor.id, "metric": f"{class_name}.count", "label": f"{monitor.name}: {label} Count"})
        options.append({"monitor_id": monitor.id, "metric": f"{class_name}.present", "label": f"{monitor.name}: {label} Present"})
    return options


def valid_monitor_metrics(monitor: MonitorConfig) -> set[str]:
    class_name = str(monitor.config.get("class_name", "person"))
    return {f"{class_name}.count", f"{class_name}.present"}


def rule_form_values(rule: RuleConfig | None) -> dict[str, object]:
    true_relay = first_relay_action(rule.true_actions if rule else [])
    false_relay = first_relay_action(rule.false_actions if rule else [])
    webhook = first_webhook_action(rule.true_actions if rule else [])
    condition = rule.condition if rule else RuleCondition("person.count", ">=", 2)
    return {
        "name": rule.name if rule else "",
        "enabled": rule.enabled if rule else True,
        "priority": rule.priority if rule else 100,
        "monitor_id": rule.monitor_id if rule else "",
        "metric": condition.metric,
        "operator": condition.operator,
        "value": condition.value,
        "true_relay_id": true_relay.relay_channel_id if true_relay else "",
        "true_relay_state": true_relay.desired_state.value if true_relay else RelayDesiredState.ON.value,
        "false_relay_id": false_relay.relay_channel_id if false_relay else "",
        "false_relay_state": false_relay.desired_state.value if false_relay else RelayDesiredState.OFF.value,
        "webhook_url": webhook.url if webhook else "",
        "webhook_snapshot_delivery": webhook.snapshot_delivery.value if webhook else SnapshotDelivery.MULTIPART.value,
        "debounce_true_ms": rule.debounce_true_ms if rule else 500,
        "debounce_false_ms": rule.debounce_false_ms if rule else 500,
        "cooldown_ms": rule.cooldown_ms if rule else 1000,
        "fail_policy": rule.fail_policy.value if rule else FailPolicy.GLOBAL.value,
    }


def first_relay_action(actions: list[object]) -> RelayAction | None:
    return next((action for action in actions if isinstance(action, RelayAction)), None)


def first_webhook_action(actions: list[object]) -> WebhookAction | None:
    return next((action for action in actions if isinstance(action, WebhookAction)), None)


def build_rule_from_form(
    *,
    id: str,
    name: str,
    enabled: bool,
    priority: int,
    monitor_id: str,
    metric: str,
    operator: str,
    value: str,
    true_relay_id: str,
    true_relay_state: RelayDesiredState,
    false_relay_id: str,
    false_relay_state: RelayDesiredState,
    webhook_url: str,
    webhook_snapshot_delivery: SnapshotDelivery,
    debounce_true_ms: int,
    debounce_false_ms: int,
    cooldown_ms: int,
    fail_policy: FailPolicy,
) -> RuleConfig:
    monitor = repo.get_monitor(monitor_id)
    if monitor is None:
        raise HTTPException(status_code=400, detail="unknown monitor")
    if metric not in valid_monitor_metrics(monitor):
        raise HTTPException(status_code=400, detail="metric does not belong to selected monitor")

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

    return RuleConfig(
        id=id,
        name=name,
        enabled=enabled,
        priority=priority,
        monitor_id=monitor_id,
        condition_group=ConditionGroup(
            mode="all",
            conditions=[RuleCondition(metric=metric, operator=operator, value=parse_condition_value(value))],
        ),
        true_actions=true_actions,
        false_actions=false_actions,
        fault_actions=fault_actions,
        debounce_true_ms=debounce_true_ms,
        debounce_false_ms=debounce_false_ms,
        cooldown_ms=cooldown_ms,
        fail_policy=fail_policy,
    )


@app.post("/ui/rules", response_class=HTMLResponse)
def create_rule(
    request: Request,
    name: str = Form(...),
    enabled: str | None = Form(None),
    priority: int = Form(100),
    monitor_id: str = Form(...),
    metric: str = Form("person.count"),
    operator: str = Form(">="),
    value: str = Form("2"),
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
    rule = build_rule_from_form(
        id=new_id("rule"),
        name=name,
        enabled=enabled == "on",
        priority=priority,
        monitor_id=monitor_id,
        metric=metric,
        operator=operator,
        value=value,
        true_relay_id=true_relay_id,
        true_relay_state=true_relay_state,
        false_relay_id=false_relay_id,
        false_relay_state=false_relay_state,
        webhook_url=webhook_url,
        webhook_snapshot_delivery=webhook_snapshot_delivery,
        debounce_true_ms=debounce_true_ms,
        debounce_false_ms=debounce_false_ms,
        cooldown_ms=cooldown_ms,
        fail_policy=fail_policy,
    )
    repo.save_rule(rule)
    if wants_fragment(request):
        return rules_table(request)
    return RedirectResponse("/rules", status_code=303)


@app.post("/ui/rules/{rule_id}", response_class=HTMLResponse)
def update_rule(
    request: Request,
    rule_id: str,
    name: str = Form(...),
    enabled: str | None = Form(None),
    priority: int = Form(100),
    monitor_id: str = Form(...),
    metric: str = Form("person.count"),
    operator: str = Form(">="),
    value: str = Form("2"),
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
    if repo.get_rule(rule_id) is None:
        raise HTTPException(status_code=404)
    rule = build_rule_from_form(
        id=rule_id,
        name=name,
        enabled=enabled == "on",
        priority=priority,
        monitor_id=monitor_id,
        metric=metric,
        operator=operator,
        value=value,
        true_relay_id=true_relay_id,
        true_relay_state=true_relay_state,
        false_relay_id=false_relay_id,
        false_relay_state=false_relay_state,
        webhook_url=webhook_url,
        webhook_snapshot_delivery=webhook_snapshot_delivery,
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
    return templates.TemplateResponse(
        request,
        "pages/relays.html",
        {
            "relays": repo.list_relays(),
            "relay_rows": repo.list_relay_rows(),
        },
    )


@app.get("/ui/relays/status", response_class=HTMLResponse)
def relay_status(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/relay_status.html",
        {"relay_rows": repo.list_relay_rows()},
    )


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
    cameras_by_id = {camera.id: camera for camera in repo.list_cameras()}
    rules_by_id = {rule.id: rule for rule in repo.list_rules()}
    return templates.TemplateResponse(
        request,
        "pages/events.html",
        {"events": event_view_rows(repo.list_events(limit=250), cameras_by_id, rules_by_id)},
    )


@app.get("/ui/events/table", response_class=HTMLResponse)
def events_table(request: Request) -> HTMLResponse:
    cameras_by_id = {camera.id: camera for camera in repo.list_cameras()}
    rules_by_id = {rule.id: rule for rule in repo.list_rules()}
    return templates.TemplateResponse(
        request,
        "partials/events_table.html",
        {"events": event_view_rows(repo.list_events(limit=250), cameras_by_id, rules_by_id)},
    )


@app.get("/api/events/{event_id}/snapshot")
def event_snapshot(event_id: str) -> FileResponse:
    event = repo.get_event(event_id)
    if event is None or not event["snapshot_path"]:
        raise HTTPException(status_code=404)
    path = Path(event["snapshot_path"])
    if not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Content-Disposition": "inline"})


@app.get("/events/{event_id}/snapshot", response_class=HTMLResponse)
def event_snapshot_page(request: Request, event_id: str) -> HTMLResponse:
    event = repo.get_event(event_id)
    if event is None or not event["snapshot_path"]:
        raise HTTPException(status_code=404)
    path = Path(event["snapshot_path"])
    if not path.exists():
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "pages/snapshot.html",
        {
            "title": "Event Snapshot",
            "subtitle": f"{event['created_at']} - {event['metric']}={event['value']}",
            "image_url": f"/api/events/{event_id}/snapshot",
        },
    )


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
    worker_status = repo.get_worker_status()
    camera_rows = repo.list_camera_rows()
    camera_faults = [row for row in camera_rows if row["health"] in {"stream_error", "auth_failed"}]
    return {
        "ok": worker_status is not None and not camera_faults,
        "cameras": len(camera_rows),
        "cameraFaults": len(camera_faults),
        "cameraFaultDetails": [
            {
                "name": row["name"],
                "health": row["health"],
                "lastError": row["last_error"],
            }
            for row in camera_faults
        ],
        "monitors": len(repo.list_monitors()),
        "rules": len(repo.list_rules()),
        "relays": len(repo.list_relays()),
        "events": len(repo.list_events(limit=1000)),
        "workerSeen": worker_status is not None,
        "workerHeartbeatAt": worker_status["heartbeat_at"] if worker_status else None,
        "inferenceMode": worker_status["inference_mode"] if worker_status else "unknown",
        "mockInference": worker_status["inference_mode"] == "mock" if worker_status else None,
        "relayControlEnabled": bool(worker_status["relay_hardware_enabled"]) if worker_status else None,
        "relayDevicesVisible": int(worker_status["relay_device_count"]) if worker_status else None,
        "db": str(settings.db_path),
        "pid": os.getpid(),
    }


@app.get("/api/system/performance")
def performance() -> dict[str, object]:
    return performance_snapshot(settings.data_dir, activate_hailo_telemetry=True)


@app.get("/healthz")
def healthz() -> dict[str, object]:
    return {"ok": True}


@app.get("/ui/system/health", response_class=HTMLResponse)
def health_panel(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/health.html", {"health": health()})


@app.get("/ui/system/performance", response_class=HTMLResponse)
def performance_panel(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/performance.html", {"performance": performance()})


@app.get("/api/live/stream")
async def live_stream() -> StreamingResponse:
    async def event_stream():
        previous = await asyncio.to_thread(repo.live_signatures)
        yield sse_message("ready", {"ok": True})
        while True:
            await asyncio.sleep(1)
            current = await asyncio.to_thread(repo.live_signatures)
            for event_name, signature in current.items():
                if signature != previous.get(event_name):
                    yield sse_message(event_name, {"changed": True})
            previous = current

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def parse_condition_value(raw: str) -> int | float | bool | str:
    normalized = raw.strip()
    if normalized.lower() == "true":
        return True
    if normalized.lower() == "false":
        return False
    try:
        return int(normalized)
    except ValueError:
        pass
    try:
        return float(normalized)
    except ValueError:
        return normalized


def normalized_zone(x: float, y: float, width: float, height: float) -> dict[str, float]:
    left = min(max(x, 0.0), 1.0)
    top = min(max(y, 0.0), 1.0)
    return {
        "x": left,
        "y": top,
        "width": min(max(width, 0.0), 1.0 - left),
        "height": min(max(height, 0.0), 1.0 - top),
    }


def event_view_rows(events, cameras_by_id: dict[str, CameraConfig], rules_by_id: dict[str, RuleConfig]) -> list[dict[str, object]]:
    rows = []
    for event in events:
        row = dict(event)
        row["camera_name"] = cameras_by_id.get(event["camera_id"], CameraConfig(event["camera_id"], event["camera_id"], "", 0, "")).name
        row["rule_name"] = rules_by_id.get(event["rule_id"], RuleConfig(event["rule_id"], event["rule_id"], True, 0, "", ConditionGroup("all", []))).name
        row["created_at_display"] = format_local_timestamp(event["created_at"])
        rows.append(row)
    return rows


def format_local_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.astimezone(LOCAL_TIMEZONE).strftime("%H:%M:%S %d/%m/%Y")


def sse_message(event_name: str, payload: dict[str, object]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
