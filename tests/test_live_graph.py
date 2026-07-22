import pytest

from app.collectors import Repository
from app.demo import dataset
from app import service


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
        return [{"id": "Demo_01"}]

    async def build_type(self, _):
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
        return [{"name": "sbom.json", "path": "artifacts/build/sbom.json", "url": "http://teamcity/sbom"}]

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_live_collection_connects_repo_config_build_push_and_sbom(monkeypatch):
    monkeypatch.setattr(service, "repository_provider", FakeBitbucket)
    monkeypatch.setattr(service, "TeamCityCollector", FakeTeamCity)
    nodes, edges = await service.collect_live()
    by_id = {node["id"]: node for node in nodes}
    relations = {(edge["source"], edge["target"], edge["relation"]) for edge in edges}

    assert by_id["build:42"]["pushedImages"][0]["image"] == "registry:5000/api:1"
    assert by_id["build:42"]["sbomArtifacts"][0]["path"] == "artifacts/build/sbom.json"
    assert ("repo:workspace/api", "build-type:Demo_01", "built_by") in relations
    assert ("build-type:Demo_01", "build:42", "ran_as") in relations
    assert ("build:42", "image:registry:5000/api:1", "pushed_image") in relations
    assert ("build:42", "artifact:build-type:Demo_01/sbom.json", "produced_sbom") in relations
    assert ("build-type:Demo_01", "image:registry:5000/api:1", "pushes") in relations
    assert ("build-type:Demo_01", "artifact:build-type:Demo_01/sbom.json", "publishes") in relations
    assert ("image:registry:5000/api:1", "artifact:build-type:Demo_01/sbom.json", "described_by") in relations
    assert ("build-type:Demo_01", "build-type:Compile", "snapshot_depends_on") in relations
    assert ("build-type:Demo_01", "build-type:Package", "uses_artifacts_from") in relations
    push_edge = next(edge for edge in edges if edge["relation"] == "pushes")
    assert "workspace/api/ci/build.sh" in push_edge["evidence"]


def test_demo_dataset_has_full_ten_repo_five_build_fixture():
    nodes, edges = dataset()
    assert len([node for node in nodes if node["kind"] == "repository"]) == 10
    assert len([node for node in nodes if node["kind"] == "tc_project"]) == 10
    assert len([node for node in nodes if node["kind"] == "build_configuration"]) == 30
    assert len([node for node in nodes if node["kind"] == "build"]) == 90
    assert len([edge for edge in edges if edge["relation"] == "ran_as"]) == 90
    assert len([edge for edge in edges if edge["relation"] == "snapshot_depends_on"]) == 20
