"""Bitbucket source traversal without SQL, migrations, scheduler or graph writes."""
import asyncio
import hashlib
import logging
import traceback
from pathlib import Path

from .collectors import repository_provider
from .config import settings
from .source_analysis import BUILD_SOURCE_PATHS, expand_scripts

log = logging.getLogger(__name__)


def fingerprint():
    root = Path(__file__).parent
    names = ("config.py", "collectors.py", "source_analysis.py", "upstream_urls.py", "dry_run.py")
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    return digest.hexdigest()[:16]


async def traverse(expected_projects, max_files):
    selected = settings.selected_bitbucket_projects
    log.debug("dry_run_start fingerprint=%s provider=%r projects=%r", fingerprint(), settings.bitbucket_provider, selected or "ALL")
    if settings.bitbucket_provider != "datacenter":
        log.error("dry_run_requires_datacenter")
        return 1
    expected = tuple(dict.fromkeys(x.strip() for x in expected_projects.split(",") if x.strip()))
    if expected_projects and (not expected or set(expected) != set(selected)):
        log.error("dry_run_scope_mismatch expected=%r effective=%r; check uppercase BITBUCKET_PROJECT_KEYS and Compose env file", expected, selected)
        return 1
    provider = repository_provider()
    repositories = files = unresolved = 0

    class Unresolved(logging.Handler):
        def emit(self, record):
            nonlocal unresolved
            if record.getMessage().startswith(("Skipping unresolved repository source path", "source_not_indexed")):
                unresolved += 1

    handler = Unresolved()
    source_log = logging.getLogger("app.source_analysis")
    source_log.addHandler(handler)
    collector_log = logging.getLogger("app.collectors")
    collector_log.addHandler(handler)
    try:
        async for repo in provider.repositories():
            if selected and repo.project_key not in selected:
                raise ValueError("Repository escaped selected project scope")
            repositories += 1
            index = await provider.file_index(repo)
            candidates = sorted(path for path in index if path in BUILD_SOURCE_PATHS
                                or Path(path).name in BUILD_SOURCE_PATHS
                                or path.lower().endswith((".sh", ".ps1", ".py")))
            if files + len(candidates) > max_files:
                log.error("dry_run_incomplete file_limit=%s; increase --max-files", max_files)
                return 1
            files += len(candidates)
            log.debug("dry_run_repository project=%r repo=%r commit=%s indexed=%s candidates=%s", repo.project_key, repo.slug, repo.revision, len(index), len(candidates))
            await expand_scripts(provider, [repo], [], candidates)
        log.info("dry_run_complete repositories=%s candidate_files=%s unresolved=%s; no graph writes; TeamCity execution context not evaluated", repositories, files, unresolved)
        return 1 if unresolved else 0
    finally:
        source_log.removeHandler(handler)
        collector_log.removeHandler(handler)
        await provider.close()


def run(timeout, expected_projects, max_files):
    from .logging_setup import configure_logging
    from .transport_security import validate_runtime_settings
    configure_logging("DEBUG")
    try:
        validate_runtime_settings()
        return asyncio.run(asyncio.wait_for(traverse(expected_projects, max_files), timeout=timeout))
    except Exception as exc:
        frames = traceback.extract_tb(exc.__traceback__)
        last = frames[-1] if frames else None
        location = f"{Path(last.filename).name}:{last.lineno}:{last.name}" if last else "unknown"
        log.error("dry_run_failed error=%s location=%s; inspect preceding DEBUG stage; no graph writes", type(exc).__name__, location)
        return 1
