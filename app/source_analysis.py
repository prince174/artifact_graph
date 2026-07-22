from dataclasses import dataclass

from .analyzer import referenced_scripts
from .collectors import Repository, RepositoryProvider


BUILD_SOURCE_PATHS = (
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "package.json", "Makefile", "makefile", "Dockerfile", "pyproject.toml",
    ".teamcity/settings.kts",
)


@dataclass(frozen=True)
class ScriptSource:
    path: str
    text: str


async def expand_scripts(provider: RepositoryProvider, repositories: list[Repository], inline_scripts: list[str]) -> list[ScriptSource]:
    sources = [ScriptSource("teamcity:inline", text) for text in inline_scripts if text]
    for repository in repositories:
        seen = set()
        pending = list(BUILD_SOURCE_PATHS) + referenced_scripts("\n".join(inline_scripts))
        while pending:
            path = pending.pop(0)
            if path in seen:
                continue
            seen.add(path)
            text = await provider.file_text(repository, path)
            if text is not None:
                sources.append(ScriptSource(f"{repository.namespace}/{repository.slug}/{path}", text))
                pending.extend(referenced_scripts(text))
    return sources
