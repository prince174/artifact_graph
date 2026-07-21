from app.analyzer import find_pushes, publishes_sbom, referenced_scripts


def test_finds_supported_pushes():
    text = "docker build . && docker push registry/a:1\npodman push registry/b:2"
    assert find_pushes(text) == [{"engine": "docker", "image": "registry/a:1"}, {"engine": "podman", "image": "registry/b:2"}]


def test_ignores_build_and_similar_words():
    assert find_pushes("docker build .\necho docker pushx nope") == []


def test_exact_sbom_artifact_rule():
    assert publishes_sbom("dist => out\n**/sbom.json => artifacts")
    assert not publishes_sbom("sbom.json => elsewhere")


def test_extracts_unique_relative_script_paths():
    script = "chmod +x ci/build.sh\n./ci/build.sh\nbash tools/release.py\n./ci/build.sh"
    assert referenced_scripts(script) == ["ci/build.sh", "tools/release.py"]
