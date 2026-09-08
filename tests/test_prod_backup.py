import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="Linux backup operator scripts")


@pytest.fixture
def backups(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    script_dir = checkout / "scripts"
    script_dir.mkdir(parents=True)
    for name in ("backup.sh", "verify-backup.sh", "restore.sh", "compose-context.sh"):
        shutil.copyfile(ROOT / "scripts" / name, script_dir / name)
    for name in ("compose.yaml", "compose.prod.yaml", ".env", ".env.prod"):
        (checkout / name).write_text("test fixture only", encoding="utf-8")
    working_dir = tmp_path / "different working directory"
    working_dir.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.jsonl"
    logger = f"#!{sys.executable}\nimport hashlib, json, os, pathlib, sys\nwith open(os.environ['COMMAND_LOG'], 'a') as log:\n log.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]) + '\\n')\n"
    (bin_dir / "git").write_text(logger + "print('v0.2-test')\n", encoding="utf-8")
    (bin_dir / "sha256sum").write_text(
        logger + "name = sys.argv[-1]\nprint(hashlib.sha256(pathlib.Path(name).read_bytes()).hexdigest() + '  ' + name)\n",
        encoding="utf-8",
    )
    (bin_dir / "docker").write_text(
        logger
        + "if 'pg_dump' in sys.argv:\n sys.stdout.buffer.write(b'FAKE-DUMP')\n sys.exit(int(os.environ.get('MOCK_DUMP_EXIT', '0')))\n"
        + "elif 'pg_restore' in sys.argv:\n sys.stdin.buffer.read()\n sys.exit(int(os.environ.get('MOCK_LIST_EXIT' if '--list' in sys.argv else 'MOCK_RESTORE_EXIT', '0')))\n"
        + "elif 'createdb' in sys.argv:\n sys.exit(int(os.environ.get('MOCK_CREATEDB_EXIT', '0')))\n",
        encoding="utf-8",
    )
    for command in bin_dir.iterdir():
        command.chmod(0o755)
    dump = working_dir / 'backup "one".dump'
    dump.write_bytes(b"VALID-TEST-DUMP")
    checksum = hashlib.sha256(dump.read_bytes()).hexdigest()
    Path(str(dump) + ".sha256").write_text(f"{checksum}  {dump.name}\n", encoding="utf-8")
    env = {
        **os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "COMMAND_LOG": str(command_log), "PYTHON_BIN": sys.executable,
        "COMPOSE_FILE": "/wrong/compose.yaml", "COMPOSE_PROJECT_NAME": "wrong-inherited-project",
    }

    def run(script, *args, **updates):
        result = subprocess.run(
            ["bash", str(script_dir / script), *map(str, args)],
            cwd=working_dir, env={**env, **updates}, capture_output=True, text=True, timeout=15,
        )
        commands = [json.loads(line) for line in command_log.read_text().splitlines()] if command_log.exists() else []
        return result, commands

    return checkout, working_dir, dump, run


def assert_context(commands, checkout, production):
    docker_commands = [command for command in commands if command[0] == "docker"]
    assert docker_commands
    for command in docker_commands:
        assert command[command.index("--project-name") + 1] == ("artefact-graph-prod" if production else "artefact-graph")
        assert command[command.index("--env-file") + 1] == str(checkout / (".env.prod" if production else ".env"))
        assert command[command.index("-f") + 1] == str(checkout / ("compose.prod.yaml" if production else "compose.yaml"))
        assert command[command.index("--project-directory") + 1] == str(checkout)
        assert "--volumes" not in command and "down" not in command
    return docker_commands


@pytest.mark.parametrize("flags,production", [((), False), (("--lab",), False), (("--prod",), True)])
def test_backup_explicitly_targets_stack_and_protects_bundle_permissions(backups, flags, production):
    checkout, working_dir, _, run = backups
    output = working_dir / 'new backup "quoted".dump'
    result, commands = run("backup.sh", *flags, output)
    assert result.returncode == 0, result.stderr
    docker_commands = assert_context(commands, checkout, production)
    assert len(docker_commands) == 1 and "pg_dump" in docker_commands[0]
    assert output.read_bytes() == b"FAKE-DUMP"
    manifest = json.loads(Path(str(output) + ".json").read_text())
    assert manifest["file"] == output.name and manifest["mode"] == ("prod" if production else "lab")
    assert manifest["project"] == ("artefact-graph-prod" if production else "artefact-graph")
    for path in (output, Path(str(output) + ".sha256"), Path(str(output) + ".json")):
        assert path.stat().st_mode & 0o777 == 0o600


def test_production_default_backup_directory_is_separate(backups):
    checkout, _, _, run = backups
    result, _ = run("backup.sh", "--prod")
    assert result.returncode == 0, result.stderr
    assert len(list((checkout / "backups/prod").glob("*.dump"))) == 1


@pytest.mark.parametrize("flags,production", [((), False), (("--lab",), False), (("--prod",), True)])
def test_verification_restores_into_temporary_database_in_selected_stack(backups, flags, production):
    checkout, _, dump, run = backups
    result, commands = run("verify-backup.sh", *flags, dump.name)
    assert result.returncode == 0, result.stderr
    docker_commands = assert_context(commands, checkout, production)
    create = next(command for command in docker_commands if "createdb" in command)
    temporary_db = create[-1]
    assert temporary_db.startswith("graph_verify_")
    restore = next(command for command in docker_commands if "pg_restore" in command)
    assert "--exit-on-error" in restore and restore[restore.index("-d") + 1] == temporary_db
    drop = next(command for command in docker_commands if "dropdb" in command)
    assert drop[-1] == temporary_db and drop[-1] != "graph"
    assert not any("stop" in command or "up" in command for command in docker_commands)


@pytest.mark.parametrize("flags,production", [((), False), (("--lab",), False), (("--prod",), True)])
def test_confirmed_restore_validates_archive_before_deleting_selected_database(backups, flags, production):
    checkout, _, dump, run = backups
    result, commands = run("restore.sh", *flags, dump.name, "--confirm")
    assert result.returncode == 0, result.stderr
    docker_commands = assert_context(commands, checkout, production)
    assert "pg_restore" in docker_commands[0] and "--list" in docker_commands[0]
    stop_at = next(i for i, command in enumerate(docker_commands) if "stop" in command)
    drop_at = next(i for i, command in enumerate(docker_commands) if "dropdb" in command)
    assert 0 < stop_at < drop_at
    restore = next(command for command in docker_commands if "pg_restore" in command and "--list" not in command)
    assert "--exit-on-error" in restore and restore[restore.index("-d") + 1] == "graph"
    assert docker_commands[-1][-4:] == ["up", "-d", "--no-deps", "graph"]


@pytest.mark.parametrize("script", ["backup.sh", "verify-backup.sh", "restore.sh"])
def test_backup_tools_reject_unknown_modes_without_docker_commands(backups, script):
    _, _, dump, run = backups
    result, commands = run(script, "--production-typo", dump.name)
    assert result.returncode == 2 and "Unknown mode" in result.stderr
    assert not commands


@pytest.mark.parametrize("arguments", [(), ("--prod",), ("--prod", "backup.dump"), ("--prod", "backup.dump", "--confirm", "extra")])
def test_restore_requires_explicit_confirmation_and_exact_arguments(backups, arguments):
    _, _, _, run = backups
    result, commands = run("restore.sh", *arguments)
    assert result.returncode == 2 and "--confirm" in result.stderr
    assert not commands


@pytest.mark.parametrize("script", ["backup.sh", "verify-backup.sh", "restore.sh"])
def test_missing_prod_environment_does_not_fall_back_to_lab(backups, script):
    checkout, _, dump, run = backups
    (checkout / ".env.prod").unlink()
    args = ("--prod", dump.name, "--confirm") if script == "restore.sh" else ("--prod", dump.name)
    result, commands = run(script, *args)
    assert result.returncode == 2 and ".env.prod" in result.stderr
    assert not commands


@pytest.mark.parametrize("script", ["verify-backup.sh", "restore.sh"])
def test_checksum_mismatch_prevents_any_database_access(backups, script):
    _, _, dump, run = backups
    dump.write_bytes(b"modified after backup")
    args = ("--prod", dump.name, "--confirm") if script == "restore.sh" else ("--prod", dump.name)
    result, commands = run(script, *args)
    assert result.returncode != 0 and "checksum mismatch" in result.stderr
    assert not any(command[0] == "docker" for command in commands)


def test_checksum_checks_selected_dump_even_if_manifest_names_another_file(backups):
    _, working_dir, dump, run = backups
    other = working_dir / "different.dump"
    other.write_bytes(b"different dump")
    other_hash = hashlib.sha256(other.read_bytes()).hexdigest()
    Path(str(dump) + ".sha256").write_text(f"{other_hash}  {other.name}\n", encoding="utf-8")
    result, commands = run("restore.sh", "--prod", dump.name, "--confirm")
    assert result.returncode != 0 and "checksum mismatch" in result.stderr
    assert not any(command[0] == "docker" for command in commands)


@pytest.mark.parametrize("content", ["", "not-a-digest", "0" * 64 + "  dump\n"])
def test_invalid_checksums_never_reach_database(backups, content):
    _, _, dump, run = backups
    Path(str(dump) + ".sha256").write_text(content, encoding="utf-8")
    result, commands = run("verify-backup.sh", "--prod", dump.name)
    assert result.returncode != 0 and "checksum" in result.stderr
    assert not any(command[0] == "docker" for command in commands)


def test_archive_list_failure_keeps_current_database_and_app_running(backups):
    _, _, dump, run = backups
    result, commands = run("restore.sh", "--prod", dump.name, "--confirm", MOCK_LIST_EXIT="1")
    assert result.returncode != 0
    assert not any("stop" in command or "dropdb" in command for command in commands)


def test_failed_restore_leaves_app_stopped_and_does_not_claim_success(backups):
    _, _, dump, run = backups
    result, commands = run("restore.sh", "--prod", dump.name, "--confirm", MOCK_RESTORE_EXIT="1")
    assert result.returncode != 0
    assert any("stop" in command for command in commands)
    assert not any("up" in command for command in commands)


def test_failed_verification_still_removes_only_its_temporary_database(backups):
    _, _, dump, run = backups
    result, commands = run("verify-backup.sh", "--prod", dump.name, MOCK_RESTORE_EXIT="1")
    assert result.returncode != 0
    created_db = next(command[-1] for command in commands if "createdb" in command)
    dropped_db = next(command[-1] for command in commands if "dropdb" in command)
    assert dropped_db == created_db and dropped_db.startswith("graph_verify_")


def test_failed_temporary_database_creation_does_not_drop_an_existing_database(backups):
    _, _, dump, run = backups
    result, commands = run("verify-backup.sh", "--prod", dump.name, MOCK_CREATEDB_EXIT="1")
    assert result.returncode != 0
    assert not any("dropdb" in command or "pg_restore" in command for command in commands)


def test_backup_does_not_overwrite_existing_dump(backups):
    _, _, dump, run = backups
    before = dump.read_bytes()
    result, commands = run("backup.sh", "--prod", dump.name)
    assert result.returncode == 2 and "Refusing to overwrite" in result.stderr
    assert dump.read_bytes() == before
    assert not commands


def test_failed_dump_does_not_publish_checksum_or_success_manifest(backups):
    _, working_dir, _, run = backups
    output = working_dir / "failed.dump"
    result, _ = run("backup.sh", "--prod", output, MOCK_DUMP_EXIT="1")
    assert result.returncode != 0 and "incomplete dump" in result.stderr
    assert not Path(str(output) + ".sha256").exists()
    assert not Path(str(output) + ".json").exists()
