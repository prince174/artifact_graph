import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.collectors import Repository
from app.demo import dataset
from app import service
from app.models import Base


@pytest.fixture(autouse=True)
def isolated_service_database(monkeypatch):
    """Keep collector tests independent from .env and any developer database."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(service, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    service.source_cache.clear()
    service.build_input_cache.clear()
    yield
    engine.dispose()


class FakeBitbucket:
    async def repositories(self):
        yield Repository(
            "bitbucket_cloud", "workspace", "DEMO", "Demo", "api", "API",
            "https://bitbucket.org/workspace/api", ("https://bitbucket.org/workspace/api.git",), "main",
        )

    async def file_text(self, repository, path, revision=None):
        return "docker push registry:5000/api:1" if path == "ci/build.sh" else None

    async def close(self):
        pass


class FakeTeamCity:
    def __init__(self, *_):
        pass

    async def build_types(self):
        return [{"id": "Root_Config"}, {"id": "Demo_01"}]

    async def build_type(self, build_type_id):
        if build_type_id == "Root_Config":
            return {"id": "Root_Config", "name": "Root config", "projectId": "_Root"}
        return {
            "id": "Demo_01", "name": "API build", "projectId": "Demo", "webUrl": "http://teamcity/config",
            "settings": {"property": [{"name": "artifactRules", "value": "**/sbom.json => artifacts"}]},
            "parameters": {"property": [{"name": "push.command", "value": "./ci/build.sh"}]},
            "steps": {"step": [{"properties": {"property": [{"name": "script.content", "value": "%push.command%"}]}}]},
            "vcs-root-entries": {"vcs-root-entry": [{"vcs-root": {"properties": {"property": [
                {"name": "url", "value": "git@bitbucket.org:workspace/api.git"},
            ]}}}]},
            "snapshot-dependencies": {"snapshot-dependency": [{"source-buildType": {"id": "Compile"}}]},
            "artifact-dependencies": {"artifact-dependency": [{"source-buildType": {"id": "Package"}}]},
        }

    async def builds(self, _):
        return [{"id": 42, "number": "5", "status": "SUCCESS", "webUrl": "http://teamcity/build/42"}]

    async def artifacts(self, _):
        return [{"name": "sbom.json", "path": "artifacts/build/sbom.json", "url": "http://teamcity/sbom", "contentHref": "/sbom"}]

    async def artifact_content(self, _):
        return '{"bomFormat":"CycloneDX","specVersion":"1.6","components":[{}]}', False

    async def build_log(self, _):
        return "docker push registry:5000/api:1\ndigest: sha256:" + "e" * 64

    async def close(self):
        pass


class FakeTeamCityWithMirror(FakeTeamCity):
    async def build_type(self, build_type_id):
        detail = await super().build_type(build_type_id)
        if build_type_id == "Demo_01":
            detail["vcs-root-entries"]["vcs-root-entry"][0]["vcs-root"]["properties"]["property"][0]["value"] = "ssh://mirror.internal/api.git"
        return detail


@pytest.mark.asyncio
async def test_live_collection_connects_repo_config_build_push_and_sbom(monkeypatch):
    monkeypatch.setattr(service, "repository_provider", FakeBitbucket)
    monkeypatch.setattr(service, "TeamCityCollector", FakeTeamCity)
    nodes, edges = await service.collect_live()
    by_id = {node["id"]: node for node in nodes}
    relations = {(edge["source"], edge["target"], edge["relation"]) for edge in edges}

    assert by_id["build:42"]["pushedImages"][0]["image"] == "registry:5000/api:1"
    assert by_id["build:42"]["sbomArtifacts"][0]["path"] == "artifacts/build/sbom.json"
    assert by_id["build:42"]["sbomArtifacts"][0]["componentCount"] == 1
    assert ("repo:workspace/api", "tc-project:Demo", "maps_to") in relations
    assert not any(node["id"] in {"tc-project:_Root", "build-type:Root_Config"} for node in nodes)
    assert ("build-type:Demo_01", "build:42", "ran_as") in relations
    assert not any(node["kind"] in {"container_image", "sbom"} for node in nodes)
    assert not any(edge["relation"] in {"pushes", "publishes", "pushed_image", "produced_sbom", "described_by"} for edge in edges)
    assert not any(relation in {"snapshot_depends_on", "uses_artifacts_from"} for _, _, relation in relations)
    assert by_id["build:42"]["pushedImages"][0]["evidence"] == "teamcity_build_log"


@pytest.mark.asyncio
async def test_manual_rule_maps_mirrored_config_and_drives_source_analysis(monkeypatch, tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text("""version: 1
mappings:
  - id: mirrored-api
    teamcity_build_type: Demo_01
    mode: replace
    repositories: [repo:workspace/api]
    reason: internal mirror
""", encoding="utf-8")
    monkeypatch.setattr(service.settings, "mapping_rules_path", str(rules))
    monkeypatch.setattr(service, "repository_provider", FakeBitbucket)
    monkeypatch.setattr(service, "TeamCityCollector", FakeTeamCityWithMirror)
    service.source_cache.clear()
    nodes, edges = await service.collect_live()
    by_id = {node["id"]: node for node in nodes}
    assert by_id["build-type:Demo_01"]["mappedRepositoryIds"] == ["repo:workspace/api"]
    assert by_id["build-type:Demo_01"]["mappingRule"]["id"] == "mirrored-api"
    assert by_id["build:42"]["hasImagePush"] is True
    assert any(edge["relation"] == "maps_to" and edge.get("confidence") == "manual" for edge in edges)


def test_demo_dataset_has_full_ten_repo_five_build_fixture():
    nodes, edges = dataset()
    assert len([node for node in nodes if node["kind"] == "repository"]) == 10
    assert len([node for node in nodes if node["kind"] == "tc_project"]) == 10
    assert len([node for node in nodes if node["kind"] == "build_configuration"]) == 30
    assert len([node for node in nodes if node["kind"] == "build"]) == 90
    assert len([edge for edge in edges if edge["relation"] == "ran_as"]) == 90
    assert not any(edge["relation"] in {"snapshot_depends_on", "uses_artifacts_from"} for edge in edges)


@pytest.mark.asyncio
async def test_source_cache_invalidates_on_revision_and_dc_server_change(monkeypatch):
    from dataclasses import replace
    class VersionedBB(FakeBitbucket):
        revision = "a" * 40
        reads = 0
        async def repositories(self):
            async for repo in super().repositories():
                yield replace(repo, provider="bitbucket_dc", revision=self.revision)
        async def file_text(self, repo, path, revision=None):
            type(self).reads += 1
            return await super().file_text(repo, path, revision)
    monkeypatch.setattr(service.settings, "bitbucket_provider", "datacenter")
    monkeypatch.setattr(service.settings, "bitbucket_url", "https://dc-one")
    monkeypatch.setattr(service, "repository_provider", VersionedBB)
    monkeypatch.setattr(service, "TeamCityCollector", FakeTeamCity)
    await service.collect_live()
    reads = VersionedBB.reads
    assert reads > 0
    await service.collect_live()
    assert VersionedBB.reads == reads
    VersionedBB.revision = "b" * 40
    await service.collect_live()
    assert VersionedBB.reads == reads * 2
    # A restarted collector must also miss a persistent cache from another DC.
    service.source_cache.clear()
    monkeypatch.setattr(service.settings, "bitbucket_url", "https://dc-two")
    await service.collect_live()
    assert VersionedBB.reads == reads * 3


@pytest.mark.asyncio
async def test_build_cache_is_scoped_to_teamcity_instance(monkeypatch):
    class CountingTC(FakeTeamCity):
        reads = 0
        async def build_log(self, build_id):
            type(self).reads += 1
            return await super().build_log(build_id)
    monkeypatch.setattr(service, "repository_provider", FakeBitbucket)
    monkeypatch.setattr(service, "TeamCityCollector", CountingTC)
    monkeypatch.setattr(service.settings, "teamcity_url", "https://tc-one")
    await service.collect_live()
    await service.collect_live()
    assert CountingTC.reads == 1
    monkeypatch.setattr(service.settings, "teamcity_url", "https://tc-two")
    await service.collect_live()
    assert CountingTC.reads == 2
