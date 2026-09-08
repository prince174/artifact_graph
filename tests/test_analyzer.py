from app.analyzer import find_executed_pushes, find_pushes, publishes_sbom, referenced_scripts, resolve_teamcity_parameters


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


def test_extracts_only_confirmed_pushes_from_teamcity_log():
    log = "\n".join([
        "[Step 1/1] docker push registry:5000/api:1",
        "[Step 1/1] digest: sha256:" + "a" * 64 + " size: 123",
        "[Step 1/1] echo docker push ignored:1",
        "[Step 1/1] podman push registry:5000/worker:2",
        "[Step 1/1] Writing manifest to image destination",
    ])
    assert find_executed_pushes(log) == [
        {"engine": "docker", "image": "registry:5000/api:1", "evidence": "teamcity_build_log", "digest": "sha256:" + "a" * 64},
        {"engine": "podman", "image": "registry:5000/worker:2", "evidence": "teamcity_build_log"},
    ]


def test_does_not_treat_command_or_failed_push_as_executed():
    assert find_executed_pushes("docker push registry/api:1") == []
    assert find_executed_pushes("docker push registry/api:1\nunauthorized: denied\ndigest: sha256:" + "b" * 64) == []


def test_matches_docker_repository_output_to_configured_tag():
    log = "The push refers to repository [registry:5000/api]\n1.7: digest: sha256:" + "c" * 64 + " size: 527"
    assert find_executed_pushes(log, [{"engine": "docker", "image": "registry:5000/api:1.7"}])[0]["image"] == "registry:5000/api:1.7"


def test_terminal_tag_selects_actual_image_independent_of_configured_candidate_order():
    configured = [{"engine": "docker", "image": f"registry:5000/api:{tag}"} for tag in ("1.0", "2.0")]
    log = "[Step 1/1] The push refers to repository [registry:5000/api]\n[Step 1/1] 2.0: digest: sha256:" + "b" * 64 + " size: 527"
    for candidates in (configured, list(reversed(configured)), []):
        assert find_executed_pushes(log, candidates) == [{
            "engine": "docker", "image": "registry:5000/api:2.0", "evidence": "teamcity_build_log", "digest": "sha256:" + "b" * 64,
        }]


def test_multiple_pushes_to_same_repository_keep_each_terminal_tag_and_digest():
    log = "\n".join([
        "The push refers to repository [registry:5000/api]", "release-1: digest: sha256:" + "a" * 64,
        "The push refers to repository [registry:5000/api]", "release-2: digest: sha256:" + "b" * 64,
    ])
    configured = [{"engine": "docker", "image": "registry:5000/api:release-1"}]
    assert [(image["image"], image["digest"]) for image in find_executed_pushes(log, configured)] == [
        ("registry:5000/api:release-1", "sha256:" + "a" * 64),
        ("registry:5000/api:release-2", "sha256:" + "b" * 64),
    ]


def test_repository_marker_retains_explicit_command_tag_when_digest_line_has_no_tag():
    log = "docker push registry:5000/api:2.0\nThe push refers to repository [registry:5000/api]\ndigest: sha256:" + "b" * 64
    configured = [{"engine": "docker", "image": f"registry:5000/api:{tag}"} for tag in ("1.0", "2.0")]
    assert find_executed_pushes(log, configured)[0]["image"] == "registry:5000/api:2.0"


def test_ambiguous_configured_tags_are_not_guessed_when_terminal_tag_is_missing():
    log = "The push refers to repository [registry:5000/api]\ndigest: sha256:" + "b" * 64
    configured = [{"engine": "docker", "image": f"registry:5000/api:{tag}"} for tag in ("1.0", "2.0")]
    assert find_executed_pushes(log, configured)[0]["image"] == "registry:5000/api"
