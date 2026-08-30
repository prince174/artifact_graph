from pathlib import Path

import pytest

from app.mapping_rules import load_mapping_rules, resolve_mapping


def write(tmp_path: Path, content: str) -> str:
    path = tmp_path / "rules.yaml"
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_load_and_apply_add_and_replace_rules(tmp_path):
    path = write(tmp_path, """version: 1
mappings:
  - id: add-repo
    teamcity_build_type: Build
    mode: add
    repositories: [repo:acme/api, repo:acme/api]
    reason: mirror URL
  - id: replace-repo
    teamcity_build_type: Deploy
    mode: replace
    repositories: []
    reason: intentionally detached
""")
    rules = load_mapping_rules(path)
    resolved, applied, missing = resolve_mapping("Build", ["repo:acme/auto"], {"repo:acme/api", "repo:acme/auto"}, rules)
    assert resolved == ["repo:acme/api", "repo:acme/auto"] and applied["mode"] == "add" and missing == []
    assert resolve_mapping("Deploy", ["repo:acme/auto"], {"repo:acme/auto"}, rules)[0] == []


def test_unknown_manual_repository_is_reported_not_linked(tmp_path):
    path = write(tmp_path, """version: 1
mappings:
  - id: missing
    teamcity_build_type: Build
    mode: replace
    repositories: [repo:acme/missing]
    reason: expected repository
""")
    resolved, _, missing = resolve_mapping("Build", [], set(), load_mapping_rules(path))
    assert resolved == [] and missing == ["repo:acme/missing"]


@pytest.mark.parametrize("content", [
    "version: 2\nmappings: []\n",
    "version: 1\nmappings:\n  - id: x\n    id: y\n",
    "version: 1\nmappings: !!python/object:os.system {}\n",
])
def test_invalid_or_unsafe_yaml_is_rejected(tmp_path, content):
    with pytest.raises(ValueError):
        load_mapping_rules(write(tmp_path, content))


def test_missing_configured_file_fails_but_empty_path_disables_rules(tmp_path):
    assert load_mapping_rules("") == {}
    with pytest.raises(ValueError, match="not found"):
        load_mapping_rules(str(tmp_path / "missing.yaml"))
