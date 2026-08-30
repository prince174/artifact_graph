from pathlib import Path
import tomllib

from app.version import __version__


ROOT = Path(__file__).parents[1]


def test_version_and_operational_assets_are_present():
    assert __version__ == "0.2.0"
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] == __version__
    for path in (
        "alembic.ini", "migrations/env.py", "migrations/versions/20260725_01_baseline.py",
        "scripts/backup.sh", "scripts/restore.sh", "scripts/verify-backup.sh", "scripts/smoke-linux.sh",
        ".github/workflows/ci.yml", "compose.e2e.yaml", "docs/production-runbook.md",
    ):
        assert (ROOT / path).is_file(), path
