import pytest

from app.collectors import Repository
from app.source_analysis import BUILD_SOURCE_PATHS, ScriptSource, build_source_paths, expand_scripts, repository_source_path


@pytest.mark.parametrize("path,expected", [
    ("./lib/log.sh", "lib/log.sh"),
    ("ci/../lib/log.sh", "lib/log.sh"),
    ("..lib/log.sh", "..lib/log.sh"),
    ("../lib/log.sh", None),
    ("ci/../../lib/log.sh", None),
    ("../ci/../lib/log.sh", None),
    ("/lib/log.sh", None),
    ("C:\\lib\\log.sh", None),
    ("lib/\nlog.sh", None),
    ("%2e%2e/lib/log.sh", None),
    ("lib%2flog.sh", None),
    (".", None),
])
def test_repository_source_paths_stay_inside_checkout(path, expected):
    assert repository_source_path(path) == expected


@pytest.mark.asyncio
async def test_parent_reference_does_not_abort_scan_or_guess_script_directory(caplog):
    repo = Repository("bitbucket_dc", "AILAB", "AILAB", "AI", "agent-analytics", "Agent", "url", ())
    provider = FakeProvider({
        "ci/build.sh": "bash ../lib/log.sh\nbash ci/../lib/publish.sh",
        "lib/publish.sh": "docker push registry/app:1\nbash ./ci/build.sh",
    })
    sources = await expand_scripts(provider, [repo], ["bash ./ci/build.sh"])
    assert provider.requests == [("agent-analytics", "ci/build.sh"), ("agent-analytics", "lib/publish.sh")]
    assert any("docker push" in source.text for source in sources)
    assert "../lib/log.sh" in caplog.text
    assert "Skipping unresolved" in caplog.text


class FakeProvider:
    def __init__(self, files=None):
        self.requests = []
        self.files = files or {"ci/build.sh": "docker push registry/service:1"}

    async def file_text(self, repository, path, revision=None):
        self.requests.append((repository.slug, path))
        return self.files.get(path)


@pytest.mark.asyncio
async def test_expands_teamcity_script_with_repository_files():
    repo = Repository("bitbucket_cloud", "acme", "DEMO", "Demo", "service", "Service", "url", ("clone",), "main")
    provider = FakeProvider()
    result = await expand_scripts(provider, [repo], ["chmod +x ci/build.sh\n./ci/build.sh"])
    assert result == [
        ScriptSource("teamcity:inline", "chmod +x ci/build.sh\n./ci/build.sh"),
        ScriptSource("acme/service/ci/build.sh", "docker push registry/service:1"),
    ]
    assert ("service", "ci/build.sh") in provider.requests


@pytest.mark.asyncio
async def test_discovers_commands_in_build_files_and_recurses_into_scripts():
    repo = Repository("bitbucket_cloud", "acme", "DEMO", "Demo", "service", "Service", "url", ("clone",), "main")
    provider = FakeProvider({
        "pom.xml": "<command>./tools/publish.py</command>",
        "package.json": '{"scripts":{"publish":"docker push registry/npm:1"}}',
        "Makefile": "publish:\n\tpodman push registry/make:1",
        ".teamcity/settings.kts": 'scriptContent = "docker push registry/kotlin:1"',
        "tools/publish.py": 'run("docker push registry/maven:1")',
    })
    result = await expand_scripts(provider, [repo], [], BUILD_SOURCE_PATHS)
    paths = {source.path for source in result}
    assert {
        "acme/service/pom.xml", "acme/service/package.json", "acme/service/Makefile",
        "acme/service/.teamcity/settings.kts", "acme/service/tools/publish.py",
    } <= paths


def test_build_files_are_selected_only_for_runner_that_executes_them():
    assert build_source_paths("Maven2", {"pomLocation": "service/pom.xml", "goals": "verify"}) == ["service/pom.xml"]
    assert build_source_paths("simpleRunner", {"script.content": "./ci/build.sh"}) == []
    assert build_source_paths("simpleRunner", {"script.content": "mvn -Ppublish-image deploy"}) == ["pom.xml"]
    assert build_source_paths("GradleRunner", {"tasks": "build"}) == ["build.gradle", "build.gradle.kts"]
    assert build_source_paths("simpleRunner", {"script.content": "npm run publish && make deploy"}) == ["package.json", "Makefile", "makefile"]
