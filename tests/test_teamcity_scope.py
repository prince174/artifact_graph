import httpx
import pytest
import yaml
from pathlib import Path
from pydantic import ValidationError

from app.collectors import TeamCityCollector
from app.config import Settings, settings


@pytest.mark.parametrize("value,expected", [
    ("", ()), (" , ", ()), ("One, Two, One", ("One", "Two")),
])
def test_configuration_selection_normalizes_ids(value, expected):
    config = Settings(_env_file=None, teamcity_build_type_ids=value)
    assert config.selected_teamcity_build_types == expected


@pytest.mark.parametrize("value", ["id:Other", "One/Two", "One,(count:100)", "One\nTwo"])
def test_selection_rejects_locator_injection(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, teamcity_build_type_ids=value)


@pytest.mark.asyncio
async def test_selected_configs_do_not_enumerate_all(monkeypatch, caplog):
    monkeypatch.setattr(settings, "teamcity_build_type_ids", "One, Two, One")
    caplog.set_level("INFO", logger="app.collectors")
    seen = []
    def handler(request):
        seen.append(request.url.path)
        identifier = request.url.path.rsplit("id:", 1)[-1]
        assert identifier in {"One", "Two"}
        return httpx.Response(200, json={"id": identifier, "name": "Configuration", "projectId": "Security"})
    collector = TeamCityCollector("https://tc/teamcity", "secret")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://tc/teamcity", transport=httpx.MockTransport(handler))
    try:
        configs = await collector.build_types()
        assert [item["id"] for item in configs] == ["One", "Two"]
        assert seen == ["/teamcity/app/rest/buildTypes/id:One", "/teamcity/app/rest/buildTypes/id:Two"]
        assert "teamcity_scope" in caplog.text
    finally:
        await collector.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 200])
async def test_missing_or_wrong_configuration_never_falls_back(monkeypatch, status):
    monkeypatch.setattr(settings, "teamcity_build_type_ids", "One")
    seen = []
    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(status, json={"id": "Wrong"})
    collector = TeamCityCollector("https://tc", "secret")
    await collector.client.aclose()
    collector.client = httpx.AsyncClient(base_url="https://tc", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPError):
            await collector.build_types()
        assert seen == ["/app/rest/buildTypes/id:One"]
    finally:
        await collector.close()


@pytest.mark.parametrize("filename", ["compose.yaml", "compose.prod.yaml", "compose.prod.external-db.yaml"])
def test_compose_passes_configuration_scope(filename):
    document = yaml.safe_load((Path(__file__).parents[1] / filename).read_text())
    assert document["services"]["graph"]["environment"]["TEAMCITY_BUILD_TYPE_IDS"] == "${TEAMCITY_BUILD_TYPE_IDS:-}"
