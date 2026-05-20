import base64
import json

from backend.app.domain import Observation, SnapshotDelivery, SnapshotRef, WebhookAction
from backend.app.webhooks.service import WebhookBuilder


def snapshot_ref() -> SnapshotRef:
    return SnapshotRef(
        event_id="evt-1",
        path="/tmp/evt-1.jpg",
        mime_type="image/jpeg",
        filename="evt-1.jpg",
        sha256="abc123",
        bytes_len=3,
    )


def test_webhook_base64_json_includes_snapshot_bytes() -> None:
    action = WebhookAction("https://example.test/hook", SnapshotDelivery.BASE64_JSON)
    prepared = WebhookBuilder().prepare(
        action,
        event_id="evt-1",
        camera={"id": "cam-1", "name": "Entrance"},
        rule={"id": "rule-1", "name": "Mantrap", "state": "true"},
        observation=Observation(
            "core.object_count",
            "cam-1",
            "person.count",
            2,
            metadata={"detections": [{"className": "person"}]},
        ),
        snapshot=snapshot_ref(),
        snapshot_bytes=b"jpg",
    )

    payload = json.loads(prepared.body)

    assert prepared.headers["Content-Type"] == "application/json"
    assert payload["snapshotBase64"] == base64.b64encode(b"jpg").decode("ascii")
    assert payload["snapshot"]["filename"] == "evt-1.jpg"
    assert payload["observation"]["metadata"]["detections"] == [{"className": "person"}]


def test_webhook_multipart_includes_json_and_file() -> None:
    action = WebhookAction("https://example.test/hook", SnapshotDelivery.MULTIPART)
    prepared = WebhookBuilder().prepare(
        action,
        event_id="evt-1",
        camera={"id": "cam-1", "name": "Entrance"},
        rule={"id": "rule-1", "name": "Mantrap", "state": "true"},
        observation=Observation("core.object_count", "cam-1", "person.count", 2),
        snapshot=snapshot_ref(),
        snapshot_bytes=b"jpg",
    )

    assert prepared.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="event"' in prepared.body
    assert b'name="snapshot"; filename="evt-1.jpg"' in prepared.body
    assert b"jpg" in prepared.body


def test_webhook_signature_covers_body() -> None:
    action = WebhookAction("https://example.test/hook", SnapshotDelivery.URL_ONLY, hmac_secret="secret")
    prepared = WebhookBuilder().prepare(
        action,
        event_id="evt-1",
        camera={"id": "cam-1", "name": "Entrance"},
        rule={"id": "rule-1", "name": "Mantrap", "state": "true"},
        observation=Observation("core.object_count", "cam-1", "person.count", 2),
        snapshot=snapshot_ref(),
        snapshot_bytes=None,
    )

    assert prepared.headers["X-SmartAI-Signature"].startswith("sha256=")
