import pytest

from app.collectors import Repository
from app.source_analysis import BUILD_SOURCE_PATHS, ScriptSource, build_source_paths, expand_scripts


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
