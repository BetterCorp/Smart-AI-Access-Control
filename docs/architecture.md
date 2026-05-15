# Architecture

The application is split into a web API process and a worker process.

- The API process renders the HTMX web interface and exposes small JSON endpoints.
- The worker process owns RTSP ingest, inference scheduling, rule evaluation, relay output, webhooks, and snapshots.
- Hardware edges are wrapped behind small services so rules and safety behavior can be tested without a Raspberry Pi.

The inference scheduler is intentionally latest-frame based. Cameras can push 25 FPS, but analytics samples at a configured low FPS and drops stale frames instead of building queues.

Relay output is arbitrated by rule priority. Equal-priority conflicts are invalid because a safety appliance must not hide ambiguous output state.

## Rule Engine Model

Rules do not run AI directly. The flow is:

```text
Camera -> AI Monitor -> typed metrics -> Rule -> Outputs
```

An AI monitor represents a monitoring function such as `Person Counter` or `Weapon Visibility`. Each monitor is attached to one camera and publishes named metrics:

- `Person Counter` is backed by a YOLOv8 object detector.
- `Weapon Visibility` is intended to be backed by a custom YOLO-family object detector.
- `person.count`: number
- `person.present`: boolean
- `weapon.visible`: boolean
- `weapon.count`: number

Rules bind to one monitor and contain a condition group. The rule engine evaluates the monitor's latest observations with type-appropriate operators:

- numeric/string: `==`, `!=`, `>`, `>=`, `<`, `<=`
- boolean: `is_true`, `is_false`
- presence: `exists`

Actions are separate from conditions. Relay output, webhooks, snapshots, debounce, cooldown, and fail policy are rule-output concerns, not AI model concerns.

## Live UI

The browser keeps an authenticated SSE connection to `/api/live/stream`.

The API process polls persisted state signatures and emits change events when monitor outputs, monitor debug snapshots, events, relays, or health-relevant state change. Browser TypeScript refreshes only the affected server-rendered fragments, so the worker and API can remain separate processes without an external broker.

## Debug Snapshots

An inference provider returns `InferenceResult`, which contains typed observations plus an optional debug JPEG from the exact analyzed frame. Mock inference intentionally returns no debug image. A real Hailo provider should attach an annotated debug frame so operator-visible counts can be compared against what the detector actually saw.

## Inference Boundary

Development uses `MockInferenceProvider`, which produces deterministic person counts for tests and local runs.

Production person counting is implemented behind `backend.app.inference.hailo.HailoGStreamerProvider`. It starts one background RTSP/GStreamer session per active person monitor, translates Hailo detection metadata into the shared `Observation` type, and exposes the latest result to the worker loop. The rest of the system does not need to know whether observations came from mock inference, Hailo, or a future plugin.

The first hardware validation target is one RTSP camera at low resolution and 1-5 analytics FPS. Scale to more cameras only after measuring decode load, Hailo latency, CPU temperature, and memory pressure.
