import httpx
import pytest

from app.collectors import TeamCityCollector


class Stream(httpx.AsyncByteStream):
    def __init__(self, count):
        self.count, self.read, self.closed = count, 0, False

    async def __aiter__(self):
        for _ in range(self.count):
            self.read += 1
            yield b"x" * 1024

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("count,truncated,read", [(1, False, 1), (1000, True, 2)])
async def test_stream_stops_at_limit_and_closes(count, truncated, read):
    stream = Stream(count)
    collector = TeamCityCollector("https://tc.example", "dummy")
    await collector.client.aclose()
    def handler(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, stream=stream)
    collector.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # Preserve the base URL used by the collector's same-origin validation.
    collector.client.base_url = "https://tc.example"
    try:
        assert await collector.artifact_content("/sbom.json", 1024) == ("x" * 1024, truncated)
        assert stream.read == read
        assert stream.closed
    finally:
        await collector.close()


@pytest.mark.asyncio
async def test_compressed_response_is_rejected_without_reading_body():
    stream = Stream(1000)
    collector = TeamCityCollector("https://tc.example", "dummy")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://tc.example", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=stream)))
    try:
        with pytest.raises(httpx.DecodingError):
            await collector.artifact_content("/sbom.json", 1024)
        assert stream.read == 0
        assert stream.closed
    finally:
        await collector.close()
