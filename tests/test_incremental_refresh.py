import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import service
from app.models import Base, Edge, Node, Scan


@pytest.fixture
def sqlite_session(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(service, "SessionLocal", session)
    return session


def test_incremental_save_updates_existing_and_removes_stale(sqlite_session):
    service._save(
        [{"id": "n1", "kind": "repository", "label": "Old"}, {"id": "n2", "kind": "build", "label": "#1"}],
        [{"source": "n1", "target": "n2", "relation": "ran_as"}],
    )
    service._save([{"id": "n1", "kind": "repository", "label": "New", "url": "https://example/repo"}], [])
    with sqlite_session() as db:
        assert [(row.id, row.label, json.loads(row.data)) for row in db.query(Node).all()] == [
            ("n1", "New", {"url": "https://example/repo"}),
        ]
        assert db.query(Edge).count() == 0


@pytest.mark.asyncio
async def test_failed_refresh_serves_previous_graph_as_degraded(sqlite_session, monkeypatch):
    service._save([{"id": "stable", "kind": "repository", "label": "Stable"}], [])

    async def fail():
        raise RuntimeError("temporary upstream failure")

    monkeypatch.setattr(service.settings, "app_mode", "live")
    monkeypatch.setattr(service, "collect_live", fail)
    await service.refresh()
    with sqlite_session() as db:
        assert db.get(Node, "stable").label == "Stable"
        assert json.loads(db.get(Node, "stable").data)["stale"] is True
        scan = db.query(Scan).one()
        assert scan.status == "degraded"
        assert json.loads(scan.details)["errorType"] == "RuntimeError"


@pytest.mark.asyncio
async def test_failed_first_refresh_still_fails(sqlite_session, monkeypatch):
    async def fail():
        raise RuntimeError("no upstream and no cache")

    monkeypatch.setattr(service.settings, "app_mode", "live")
    monkeypatch.setattr(service, "collect_live", fail)
    with pytest.raises(RuntimeError):
        await service.refresh()
    with sqlite_session() as db:
        assert db.query(Scan).one().status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["collectionError", "mappingUnavailable"])
async def test_first_partial_scan_is_degraded_not_success(sqlite_session, monkeypatch, failure):
    async def partial():
        return [{"id": "cfg", "kind": "build_configuration", "label": "Build", failure: "HTTPStatusError"}], []
    monkeypatch.setattr(service.settings, "app_mode", "live")
    monkeypatch.setattr(service, "collect_live", partial)
    await service.refresh()
    with sqlite_session() as db:
        scan = db.query(Scan).one()
        assert scan.status == "degraded"
        assert json.loads(scan.details)["incompleteEntities"] == 1
