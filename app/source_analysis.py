from dataclasses import dataclass

from .analyzer import referenced_scripts
from .collectors import Repository, RepositoryProvider


@dataclass(frozen=True)
class ScriptSource:
    path: str
    text: str


async def expand_scripts(provider: RepositoryProvider, repositories: list[Repository], inline_scripts: list[str]) -> list[ScriptSource]:
    sources = [ScriptSource("teamcity:inline", text) for text in inline_scripts if text]
    paths = referenced_scripts("\n".join(inline_scripts))
    for repository in repositories:
        for path in paths:
            text = await provider.file_text(repository, path)
            if text is not None:
                sources.append(ScriptSource(f"{repository.namespace}/{repository.slug}/{path}", text))
    return sources
