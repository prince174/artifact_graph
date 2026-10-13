from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.scheduling import schedule_refresh


@pytest.mark.parametrize("now,expected", [
    ("2026-10-13T23:59:59+00:00", "2026-10-14T03:00:00+03:00"),
    ("2026-10-14T00:00:00+00:00", "2026-10-14T03:00:00+03:00"),
    ("2026-10-14T00:00:01+00:00", "2026-10-15T03:00:00+03:00"),
    ("2026-12-31T20:00:00-05:00", "2027-01-02T03:00:00+03:00"),
    ("2026-07-01T04:00:00+05:00", "2026-07-01T03:00:00+03:00"),
])
def test_daily_moscow_refresh_independent_of_host_timezone(now, expected):
    scheduler = AsyncIOScheduler(timezone=timezone(timedelta(hours=-8)))
    job = schedule_refresh(scheduler, lambda: None)
    fire = job.trigger.get_next_fire_time(None, datetime.fromisoformat(now))
    assert fire.isoformat() == expected
    following = job.trigger.get_next_fire_time(fire, fire + timedelta(seconds=1))
    assert following - fire == timedelta(days=1)
    assert following.hour == 3
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.misfire_grace_time == 300
