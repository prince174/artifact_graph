from .scan_progress import scan_progress
from .config import settings
from .concurrency import bounded_results
from dataclasses import dataclass
import logging
import posixpath
from urllib.parse import unquote

from .analyzer import referenced_scripts
from .collectors import Repository, RepositoryProvider


log = logging.getLogger(__name__)


def repository_source_path(path: str) -> str | None:
    """Normalize checkout-relative paths without guessing a shell working directory."""
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return None
    # Reject encoded path syntax rather than reinterpret a repository filename.
    if unquote(path) != path:
        return None
    parts = []
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return posixpath.join(*parts) if parts else None


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
        scan_progress.update(stage="sources", repository=repository.slug)
        log.info("source_analysis_start project=%r repo=%r commit=%s", repository.project_key, repository.slug, repository.revision)
        seen = set()
        pending = list(source_paths) + referenced_scripts("\n".join(inline_scripts))
        while pending:
            paths = []
            for raw_path in pending:
                path = repository_source_path(raw_path)
                if path is None:
                    log.warning("Skipping unresolved repository source path %r in %s/%s: "
                                "outside checkout or unsupported path syntax; working directory is unknown",
                                raw_path, repository.namespace, repository.slug)
                elif path not in seen:
                    seen.add(path)
                    paths.append(path)
            pending = []
            # Populate the immutable file index once before parallel raw reads.
            if paths and hasattr(provider, "file_index"):
                await provider.file_index(repository)
            async def read(path):
                return await provider.file_text(repository, path)
            async for _, path, text in bounded_results(paths, read, settings.bitbucket_concurrency):
                if text is not None:
                    sources.append(ScriptSource(f"{repository.namespace}/{repository.slug}/{path}", text))
                    pending.extend(referenced_scripts(text))
        log.info("source_analysis_complete project=%r repo=%r paths_checked=%s", repository.project_key, repository.slug, len(seen))
    return sources
