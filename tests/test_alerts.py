import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import alerts
from app.models import Base, GraphSnapshot, Scan, WebhookDelivery
from app.snapshots import canonical_graph


@pytest.fixture
def alert_db(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(alerts, "SessionLocal", session)
    monkeypatch.setattr(alerts.settings, "webhook_enabled", True)
    monkeypatch.setattr(alerts.settings, "webhook_url", "http://receiver.test/hook")
    monkeypatch.setattr(alerts.settings, "webhook_secret", "s" * 32)
    monkeypatch.setattr(alerts.settings, "webhook_allow_http", True)
    return session


def add_scan(db, status, nodes=None):
    scan = Scan(status=status, message=status, details="{}")
    db.add(scan); db.flush()
    if nodes is not None:
        payload, digest = canonical_graph(nodes, [])
        db.add(GraphSnapshot(scan_id=scan.id, node_count=len(nodes), edge_count=0, content_hash=digest, payload=payload))
    return scan


def test_outbox_deduplicates_degraded_episode_and_queues_recovery(alert_db):
    with alert_db.begin() as db:
        first = add_scan(db, "degraded")
        assert alerts.enqueue_scan_alerts(db, first) == [f"scan.degraded:{first.id}"]
        assert alerts.enqueue_scan_alerts(db, first) == []
    with alert_db.begin() as db:
        second = add_scan(db, "degraded")
        assert alerts.enqueue_scan_alerts(db, second) == []
    with alert_db.begin() as db:
        recovered = add_scan(db, "success", [])
        assert alerts.enqueue_scan_alerts(db, recovered) == [f"scan.recovered:{recovered.id}"]


def test_output_alert_contains_only_safe_counts(alert_db):
    before = [{"id": "build:1", "kind": "build", "hasImagePush": False, "hasSbom": False}]
    after = [{"id": "build:1", "kind": "build", "hasImagePush": True, "hasSbom": True, "pushedImages": [{"image": "secret.registry/repo:1"}], "sbomArtifacts": [{"path": "sbom.json"}]}]
    with alert_db.begin() as db:
        old = add_scan(db, "success", before)
        alerts.enqueue_scan_alerts(db, old)
    with alert_db.begin() as db:
        current = add_scan(db, "success", after)
        alerts.enqueue_scan_alerts(db, current)
    with alert_db() as db:
        row = db.query(WebhookDelivery).filter_by(event_type="graph.outputs.changed").one()
        payload = json.loads(row.payload)
        assert payload["changes"][0]["after"] == {"hasImagePush": True, "hasSbom": True, "imageCount": 1, "sbomCount": 1}
        assert "secret.registry" not in row.payload


@pytest.mark.asyncio
async def test_dispatch_signs_delivery_and_marks_it_sent(alert_db):
    with alert_db.begin() as db:
        scan = add_scan(db, "degraded")
        alerts.enqueue_scan_alerts(db, scan)

    def handler(request):
        assert request.headers["x-artifact-signature"] == alerts.signature(request.content, request.headers["x-artifact-timestamp"], request.headers["x-artifact-event-id"])
        return httpx.Response(204)

    assert await alerts.dispatch_webhooks(httpx.MockTransport(handler)) == 1
    with alert_db() as db:
        row = db.query(WebhookDelivery).one()
        assert row.status == "sent" and row.attempts == 1 and row.sent_at is not None


@pytest.mark.asyncio
async def test_retryable_failure_stays_pending_without_affecting_scan(alert_db):
    with alert_db.begin() as db:
        scan = add_scan(db, "degraded")
        alerts.enqueue_scan_alerts(db, scan)
    assert await alerts.dispatch_webhooks(httpx.MockTransport(lambda _: httpx.Response(503))) == 0
    with alert_db() as db:
        row = db.query(WebhookDelivery).one()
        assert row.status == "pending" and row.attempts == 1 and row.last_status == "HTTP 503"
