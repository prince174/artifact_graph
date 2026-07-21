import pytest

from app.collectors import Repository
from app.source_analysis import ScriptSource, expand_scripts


class FakeProvider:
    def __init__(self):
        self.requests = []

    async def file_text(self, repository, path, revision=None):
        self.requests.append((repository.slug, path))
        return "docker push registry/service:1" if path == "ci/build.sh" else None


@pytest.mark.asyncio
async def test_expands_teamcity_script_with_repository_files():
    repo = Repository("bitbucket_cloud", "acme", "DEMO", "Demo", "service", "Service", "url", ("clone",), "main")
    provider = FakeProvider()
    result = await expand_scripts(provider, [repo], ["chmod +x ci/build.sh\n./ci/build.sh"])
    assert result == [
        ScriptSource("teamcity:inline", "chmod +x ci/build.sh\n./ci/build.sh"),
        ScriptSource("acme/service/ci/build.sh", "docker push registry/service:1"),
    ]
    assert provider.requests == [("service", "ci/build.sh")]
