"""Daily graph refresh in Moscow time, independent of the host timezone."""
from datetime import timedelta, timezone

from apscheduler.triggers.cron import CronTrigger


MOSCOW = timezone(timedelta(hours=3), name="MSK")


def schedule_refresh(scheduler, refresh):
    return scheduler.add_job(
        refresh, CronTrigger(hour=3, minute=0, second=0, timezone=MOSCOW),
        id="refresh", max_instances=1, coalesce=True, misfire_grace_time=300,
    )
