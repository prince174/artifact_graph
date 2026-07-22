from app.analyzer import find_pushes, publishes_sbom, referenced_scripts, resolve_teamcity_parameters


def test_finds_supported_pushes():
    text = "docker build . && docker push registry/a:1\npodman push registry/b:2"
    assert find_pushes(text) == [{"engine": "docker", "image": "registry/a:1"}, {"engine": "podman", "image": "registry/b:2"}]


def test_ignores_build_and_similar_words():
    assert find_pushes("docker build .\necho docker pushx nope") == []


def test_exact_sbom_artifact_rule():
    assert publishes_sbom("dist => out\n**/sbom.json => artifacts")
    assert not publishes_sbom("sbom.json => elsewhere")


def test_finds_push_in_maven_plugin_profile_and_resolves_property():
    pom = """<project><properties><docker.image>registry:5000/maven-api:2</docker.image></properties>
    <profiles><profile><build><plugins><plugin><configuration><arguments>
    <argument>docker</argument><argument>push</argument><argument>${docker.image}</argument>
    </arguments><commandline>sh -c &quot;podman push registry:5000/sidecar:2&quot;</commandline>
    </configuration></plugin></plugins></build></profile></profiles></project>"""
    assert find_pushes(pom) == [
        {"engine": "docker", "image": "registry:5000/maven-api:2"},
        {"engine": "podman", "image": "registry:5000/sidecar:2"},
    ]


def test_resolves_nested_teamcity_parameters():
    parameters = {"registry": "registry:5000", "image": "%registry%/service:1"}
    assert resolve_teamcity_parameters("docker push %image%", parameters) == "docker push registry:5000/service:1"


def test_extracts_unique_relative_script_paths():
    script = "chmod +x ci/build.sh\n./ci/build.sh\nbash tools/release.py\n./ci/build.sh"
    assert referenced_scripts(script) == ["ci/build.sh", "tools/release.py"]
