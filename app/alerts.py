import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from .config import settings
from .models import GraphSnapshot, Scan, SessionLocal, WebhookDelivery
from .snapshots import diff_graphs


def validate_webhook_settings() -> None:
    if not settings.webhook_enabled:
        return
    parsed = urlsplit(settings.webhook_url)
    if parsed.scheme not in ({"https"} if not settings.webhook_allow_http else {"http", "https"}) or not parsed.hostname:
        raise ValueError("WEBHOOK_URL must be an absolute HTTPS URL")
    if len(settings.webhook_secret) < 24:
        raise ValueError("WEBHOOK_SECRET must contain at least 24 characters")


def signature(body: bytes, timestamp: str, event_key: str) -> str:
    message = timestamp.encode() + b"." + event_key.encode() + b"." + body
    return "sha256=" + hmac.new(settings.webhook_secret.encode(), message, hashlib.sha256).hexdigest()


def enqueue_scan_alerts(db, scan: Scan) -> list[str]:
    if not settings.webhook_enabled:
        return []
    validate_webhook_settings()
    previous = db.query(Scan).filter(Scan.id < scan.id).order_by(Scan.id.desc()).first()
    events = []
    if scan.status == "degraded" and (not previous or previous.status != "degraded"):
        events.append(("scan.degraded", {"status": scan.status, "message": scan.message, "upstream": json.loads(scan.details or "{}")}))
    elif scan.status == "success" and previous and previous.status == "degraded":
        events.append(("scan.recovered", {"status": scan.status, "previousScanId": previous.id}))
    if scan.status == "success":
        current = db.query(GraphSnapshot).filter_by(scan_id=scan.id).first()
        prior = (
            db.query(GraphSnapshot).join(Scan, GraphSnapshot.scan_id == Scan.id)
            .filter(Scan.status == "success", Scan.id < scan.id).order_by(Scan.id.desc()).first()
        )
        if current and prior:
            changes = _safe_output_changes(diff_graphs(json.loads(prior.payload), json.loads(current.payload)))
            if changes:
                events.append(("graph.outputs.changed", {"previousSnapshotId": prior.id, "snapshotId": current.id, "changes": changes[:100], "truncated": len(changes) > 100}))
    keys = []
    for event_type, data in events:
        event_key = f"{event_type}:{scan.id}"
        if db.query(WebhookDelivery).filter_by(event_key=event_key).first():
            continue
        payload = {"event": event_type, "eventId": event_key, "scanId": scan.id, "createdAt": datetime.now(timezone.utc).isoformat(), **data}
        db.add(WebhookDelivery(event_key=event_key, event_type=event_type, payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))
        keys.append(event_key)
    return keys


def _safe_output_changes(diff: dict) -> list[dict]:
    changes = []
    for item in diff.get("outputChanges", []):
        changes.append({"id": item["id"], "before": _output_summary(item["before"]), "after": _output_summary(item["after"])})
    for side, state in (("added", "after"), ("removed", "before")):
        for node in diff.get("details", {}).get(side, []):
            if node.get("kind") == "build" and (node.get("hasImagePush") or node.get("hasSbom")):
                changes.append({"id": node["id"], "before": None if state == "after" else _output_summary(node), "after": _output_summary(node) if state == "after" else None})
    return sorted(changes, key=lambda item: item["id"])


def _output_summary(value: dict) -> dict:
    return {"hasImagePush": bool(value.get("hasImagePush")), "hasSbom": bool(value.get("hasSbom")), "imageCount": len(value.get("pushedImages") or []), "sbomCount": len(value.get("sbomArtifacts") or [])}


async def dispatch_webhooks(transport=None) -> int:
    if not settings.webhook_enabled:
        return 0
    validate_webhook_settings()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        ids = [row.id for row in db.query(WebhookDelivery).filter(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= now).order_by(WebhookDelivery.id).limit(20).all()]
    sent = 0
    async with httpx.AsyncClient(verify=settings.webhook_verify_tls, timeout=settings.webhook_timeout_seconds, follow_redirects=False, transport=transport) as client:
        for delivery_id in ids:
            with SessionLocal() as db:
                row = db.get(WebhookDelivery, delivery_id)
                if not row or row.status != "pending":
                    continue
                body, timestamp = row.payload.encode(), str(int(time.time()))
                headers = {"Content-Type": "application/json", "X-Artifact-Event": row.event_type, "X-Artifact-Event-Id": row.event_key, "X-Artifact-Timestamp": timestamp, "X-Artifact-Signature": signature(body, timestamp, row.event_key)}
                try:
                    response = await client.post(settings.webhook_url, content=body, headers=headers)
                    row.attempts += 1
                    if 200 <= response.status_code < 300:
                        row.status, row.last_status, row.sent_at = "sent", str(response.status_code), now
                        sent += 1
                    elif response.status_code in {408, 425, 429} or response.status_code >= 500:
                        _retry_or_dead(row, f"HTTP {response.status_code}", now)
                    else:
                        row.status, row.last_status = "dead", f"HTTP {response.status_code}"
                except httpx.TransportError as exc:
                    row.attempts += 1
                    _retry_or_dead(row, type(exc).__name__, now)
                db.commit()
    return sent


def _retry_or_dead(row: WebhookDelivery, reason: str, now: datetime) -> None:
    row.last_status = reason
    if row.attempts >= settings.webhook_max_attempts:
        row.status = "dead"
    else:
        row.next_attempt_at = now + timedelta(minutes=min(60, 2 ** max(0, row.attempts - 1)))
