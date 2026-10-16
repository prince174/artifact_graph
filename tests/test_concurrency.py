import asyncio

import pytest

from app.concurrency import bounded_results


@pytest.mark.asyncio
async def test_work_is_parallel_bounded_and_ordered():
    active = peak = 0
    async def work(value):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.005)
        active -= 1
        return value * 2
    result = [item async for item in bounded_results(list(range(10)), work, 3)]
    assert peak == 3 and active == 0
    assert result == [(i + 1, i, i * 2) for i in range(10)]


@pytest.mark.asyncio
async def test_failure_cancels_and_awaits_inflight_requests():
    active = 0
    async def work(value):
        nonlocal active
        active += 1
        try:
            await asyncio.sleep(.001 if value == 0 else 30)
            raise ValueError("failed")
        finally:
            active -= 1
    with pytest.raises(ValueError):
        async for _ in bounded_results([0, 1, 2], work, 3):
            pass
    assert active == 0
