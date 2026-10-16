"""Bounded batches with deterministic output and cancellation cleanup."""
import asyncio


async def bounded_results(items, operation, limit):
    for offset in range(0, len(items), limit):
        batch = items[offset:offset + limit]
        tasks = [asyncio.create_task(operation(item)) for item in batch]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for index, (item, result) in enumerate(zip(batch, results), offset + 1):
            yield index, item, result
