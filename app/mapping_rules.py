from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class MappingRule:
    id: str
    teamcity_build_type: str
    mode: str
    repositories: tuple[str, ...]
    reason: str


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_mapping_rules(path: str) -> dict[str, MappingRule]:
    if not path:
        return {}
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"Mapping rules file not found: {source}")
    try:
        document = yaml.load(source.read_text(encoding="utf-8"), Loader=UniqueKeyLoader) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid mapping rules YAML: {exc}") from exc
    if set(document) - {"version", "mappings"} or document.get("version") != 1 or not isinstance(document.get("mappings", []), list):
        raise ValueError("Mapping rules require version: 1 and a mappings list")
    result, ids = {}, set()
    allowed = {"id", "teamcity_build_type", "mode", "repositories", "reason"}
    for raw in document.get("mappings", []):
        if not isinstance(raw, dict) or set(raw) != allowed:
            raise ValueError(f"Mapping rule must contain exactly: {', '.join(sorted(allowed))}")
        if raw["mode"] not in {"add", "replace"} or not isinstance(raw["repositories"], list):
            raise ValueError("Mapping rule mode must be add/replace and repositories must be a list")
        if not all(isinstance(raw[key], str) and raw[key].strip() for key in ("id", "teamcity_build_type", "reason")):
            raise ValueError("Mapping rule id, teamcity_build_type and reason are required strings")
        if not all(isinstance(item, str) and item.startswith("repo:") for item in raw["repositories"]):
            raise ValueError("Mapping rule repositories must use stable repo: IDs")
        if raw["id"] in ids or raw["teamcity_build_type"] in result:
            raise ValueError("Mapping rule IDs and teamcity_build_type values must be unique")
        ids.add(raw["id"])
        result[raw["teamcity_build_type"]] = MappingRule(raw["id"], raw["teamcity_build_type"], raw["mode"], tuple(dict.fromkeys(raw["repositories"])), raw["reason"])
    return result


def resolve_mapping(build_type_id: str, automatic: list[str], repository_ids: set[str], rules: dict[str, MappingRule]) -> tuple[list[str], dict | None, list[str]]:
    rule = rules.get(build_type_id)
    if not rule:
        return sorted(set(automatic)), None, []
    missing = sorted(repo_id for repo_id in rule.repositories if repo_id not in repository_ids)
    manual = {repo_id for repo_id in rule.repositories if repo_id in repository_ids}
    resolved = manual if rule.mode == "replace" else set(automatic) | manual
    return sorted(resolved), {"id": rule.id, "mode": rule.mode, "reason": rule.reason}, missing
