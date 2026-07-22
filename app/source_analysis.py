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


def build_source_paths(step_type: str, properties: dict[str, str]) -> list[str]:
    runner = (step_type or "").lower()
    text = "\n".join(properties.values()).lower()
    paths = []
    if "maven" in runner or "mvn " in text or text.strip().startswith("mvn"):
        paths.append(properties.get("pomLocation") or properties.get("pom.location") or "pom.xml")
    if "gradle" in runner or "gradle" in text:
        paths.extend(["build.gradle", "build.gradle.kts"])
    if "node" in runner or "npm " in text or "yarn " in text or "pnpm " in text:
        paths.append("package.json")
    if "make" in runner or "make " in text or text.strip().startswith("make"):
        paths.extend(["Makefile", "makefile"])
    if ".teamcity/settings.kts" in text:
        paths.append(".teamcity/settings.kts")
    return list(dict.fromkeys(paths))


async def expand_scripts(provider: RepositoryProvider, repositories: list[Repository], inline_scripts: list[str], source_paths=()) -> list[ScriptSource]:
    sources = [ScriptSource("teamcity:inline", text) for text in inline_scripts if text]
    for repository in repositories:
        seen = set()
        pending = list(source_paths) + referenced_scripts("\n".join(inline_scripts))
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
