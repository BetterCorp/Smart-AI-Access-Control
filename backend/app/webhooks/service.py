from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.generator import _make_boundary

from backend.app.domain import Observation, SnapshotDelivery, SnapshotRef, WebhookAction


@dataclass(frozen=True)
class PreparedWebhook:
    url: str
    headers: dict[str, str]
    body: bytes
    timeout_ms: int
    delivery: SnapshotDelivery


class WebhookBuilder:
    def prepare(
        self,
        action: WebhookAction,
        *,
        event_id: str,
        camera: dict[str, str],
        rule: dict[str, str],
        observation: Observation,
        snapshot: SnapshotRef | None,
        snapshot_bytes: bytes | None,
    ) -> PreparedWebhook:
        payload = self._payload(event_id, camera, rule, observation, snapshot)

        if action.snapshot_delivery == SnapshotDelivery.MULTIPART and snapshot is not None and snapshot_bytes is not None:
            content_type, body = self._multipart_body(payload, snapshot, snapshot_bytes)
        else:
            if action.snapshot_delivery == SnapshotDelivery.BASE64_JSON and snapshot_bytes is not None:
                payload["snapshotBase64"] = base64.b64encode(snapshot_bytes).decode("ascii")
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            content_type = "application/json"

        headers = dict(action.headers)
        headers["Content-Type"] = content_type
        if action.hmac_secret:
            signature = hmac.new(action.hmac_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            headers["X-SmartAI-Signature"] = f"sha256={signature}"

        return PreparedWebhook(
            url=action.url,
            headers=headers,
            body=body,
            timeout_ms=action.timeout_ms,
            delivery=action.snapshot_delivery,
        )

    def _payload(
        self,
        event_id: str,
        camera: dict[str, str],
        rule: dict[str, str],
        observation: Observation,
        snapshot: SnapshotRef | None,
    ) -> dict[str, object]:
        snapshot_payload: dict[str, object] = {"available": False}
        if snapshot is not None:
            snapshot_payload = {
                "available": True,
                "url": f"/api/events/{event_id}/snapshot",
                "mimeType": snapshot.mime_type,
                "filename": snapshot.filename,
                "sha256": snapshot.sha256,
            }
        return {
            "eventId": event_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "camera": camera,
            "rule": rule,
            "observation": {
                "metric": observation.metric,
                "value": observation.value,
                "zoneId": observation.zone_id,
                "labels": observation.labels,
            },
            "snapshot": snapshot_payload,
        }

    def _multipart_body(
        self,
        payload: dict[str, object],
        snapshot: SnapshotRef,
        snapshot_bytes: bytes,
    ) -> tuple[str, bytes]:
        boundary = _make_boundary()
        lines = [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="event"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="snapshot"; filename="{snapshot.filename}"\r\n'.encode(),
            f"Content-Type: {snapshot.mime_type}\r\n\r\n".encode(),
            snapshot_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        return f"multipart/form-data; boundary={boundary}", b"".join(lines)

