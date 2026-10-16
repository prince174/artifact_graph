"""Incremental TeamCity history with overlap and periodic full reconciliation."""
from datetime import datetime, timedelta, timezone
import logging

log = logging.getLogger(__name__)


def build_date(build):
    for key in ("startDate", "queuedDate", "finishDate"):
        try:
            return datetime.strptime(build[key], "%Y%m%dT%H%M%S%z")
        except (KeyError, ValueError, TypeError):
            continue
    return None


async def incremental_builds(collector, configuration, cache, days, limit):
    # Limited histories are already cheap and must retain the exact server order.
    if limit:
        return await collector._builds_window(configuration)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    key = ("history-v1", str(collector.client.base_url), configuration, days)
    stored = cache.get(key)
    full = not stored or now.timestamp() - stored["fullAt"] >= 7 * 86400
    log.debug("teamcity_history configuration=%r mode=%s cached_builds=%s", configuration,
              "full" if full else "incremental", len(stored["builds"]) if stored else 0)
    if full:
        result = await collector._builds_window(configuration)
    else:
        since = max(cutoff, datetime.fromtimestamp(stored["checkedAt"], timezone.utc) - timedelta(minutes=5))
        updates = await collector._builds_window(configuration, since=since)
        by_id = {str(item["id"]): item for item in stored["builds"]}
        fresh = {str(item["id"]) for item in updates}
        for item in stored["builds"]:
            if item.get("state", "finished") != "finished" and str(item["id"]) not in fresh:
                updated = await collector._refresh_build(item["id"])
                if updated is None:
                    by_id.pop(str(item["id"]), None)
                else:
                    by_id[str(item["id"])] = updated
        by_id.update((str(item["id"]), item) for item in updates)
        result = list(by_id.values())
    result = [item for item in result if build_date(item) is None or build_date(item) >= cutoff]
    cache.put(key, {"checkedAt": now.timestamp(), "fullAt": now.timestamp() if full else stored["fullAt"], "builds": result})
    return result
