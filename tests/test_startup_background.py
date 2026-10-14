import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from app import main


@pytest.mark.asyncio
async def test_web_serves_while_initial_refresh_is_blocked(monkeypatch):
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def blocked_refresh():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(main, "refresh", blocked_refresh)
    monkeypatch.setattr(main, "validate_runtime_settings", lambda: None)
    monkeypatch.setattr(main, "scheduler", MagicMock())
    async with main.lifespan(main.app):
        await asyncio.wait_for(entered.wait(), 1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/login"), 1)
            assert response.status_code == 200
            assert (await client.get("/health/live")).status_code == 200
        assert not cancelled.is_set()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_initial_failure_is_logged_without_stopping_web(monkeypatch):
    async def failed():
        raise RuntimeError("private upstream detail")
    monkeypatch.setattr(main, "refresh", failed)
    logger = MagicMock()
    monkeypatch.setattr(main.logging, "getLogger", lambda *args: logger)
    await main.initial_refresh()
    logger.error.assert_called_once_with("initial_refresh_failed error=%s", "RuntimeError")


def test_ready_before_any_scan(monkeypatch):
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    monkeypatch.setattr(main, "SessionLocal", factory)
    result = main.health_ready()
    assert result["status"] == "ready"
    assert result["graphReady"] is False
    assert result["lastUsableScan"] is None
