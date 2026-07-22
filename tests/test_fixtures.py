import pytest

from app.analyzer import find_pushes
from app.fixtures import REPOS, files_for


def test_every_fixture_has_buildable_minimal_content():
    assert len(REPOS) == 10
    for repo in REPOS:
        files = files_for(*repo)
        assert {"README.md", "Dockerfile", "ci/build.sh"} <= files.keys()
        assert files["ci/build.sh"].startswith("#!/bin/sh\nset -eu")


@pytest.mark.parametrize("engine", ["docker", "podman"])
def test_image_fixture_builds_before_supported_push(engine):
    files = files_for("service", "python", f"{engine} push registry:5000/service:1.0", False)
    script = files["ci/build.sh"]
    assert "docker build -t registry:5000/service:1.0 ." in script
    assert f"{engine} push registry:5000/service:1.0" in script
    assert script.index("docker build") < script.index(f"{engine} push")


def test_sbom_fixture_generates_cyclonedx_artifact():
    files = files_for("service", "python", "", True)
    assert "build/sbom.json" in files["ci/sbom.sh"]
    assert '"bomFormat":"CycloneDX"' in files["ci/sbom.sh"]


def test_maven_fixture_can_publish_image_from_pom_profile():
    files = files_for("service", "maven", "docker push registry:5000/service:1.0", False)
    assert "exec-maven-plugin" in files["pom.xml"]
    assert find_pushes(files["pom.xml"]) == [{"engine": "docker", "image": "registry:5000/service:1.0"}]


def test_rejects_unsupported_push_command():
    with pytest.raises(ValueError):
        files_for("service", "python", "skopeo copy image target", False)
