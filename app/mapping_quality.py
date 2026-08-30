from collections import defaultdict

MAPPING_KINDS = {"bb_project", "repository", "tc_project", "build_configuration"}


def annotate_mapping_quality(nodes: list[dict], edges: list[dict]) -> None:
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in edges:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)
    mapped_repositories = {edge["source"] for edge in edges if edge["relation"] == "maps_to"}
    mapped_tc_projects = {edge["target"] for edge in edges if edge["relation"] == "maps_to"}
    for node in nodes:
        mapped, reason, confidence = False, "no_vcs_mapping", None
        if node["kind"] == "repository":
            links = [edge for edge in outgoing[node["id"]] if edge["relation"] == "maps_to"]
            mapped = node["id"] in mapped_repositories
            reason = (links[0].get("reason") or links[0].get("confidence", "mapped")) if links else reason
            confidence = links[0].get("confidence") if links else None
        elif node["kind"] == "tc_project":
            links = [edge for edge in incoming[node["id"]] if edge["relation"] == "maps_to"]
            mapped = node["id"] in mapped_tc_projects
            reason = (links[0].get("reason") or links[0].get("confidence", "mapped")) if links else "no_repository_mapping"
            confidence = links[0].get("confidence") if links else None
        elif node["kind"] == "build_configuration":
            if node.get("mappingUnavailable"):
                node["mappingStatus"], node["mappingReason"] = "unknown", "teamcity_detail_unavailable"
                node.pop("mappingConfidence", None)
                continue
            mapped = bool(node.get("mappedRepositoryIds"))
            observations = node.get("mappingObservations", [])
            failures = [item for item in observations if item.get("reason") != "exact_vcs_url"]
            if rule := node.get("mappingRule"):
                missing_manual = any(item.get("reason") == "manual_repository_not_found" for item in observations)
                if rule.get("mode") == "replace":
                    if mapped and not missing_manual:
                        node["mappingStatus"], node["mappingReason"], node["mappingConfidence"] = "mapped", "manual_rule", "manual"
                    elif mapped:
                        node["mappingStatus"], node["mappingReason"], node["mappingConfidence"] = "partial", "manual_repository_not_found", "manual"
                    else:
                        node["mappingStatus"], node["mappingReason"] = "unmapped", "manual_repository_not_found" if missing_manual else "manual_exclusion"
                        node.pop("mappingConfidence", None)
                    continue
            if mapped and failures:
                node["mappingStatus"], node["mappingReason"], node["mappingConfidence"] = "partial", "partial_vcs_mapping", "mixed"
                continue
            reason = "exact_vcs_url" if mapped else (observations[0].get("reason", "vcs_url_not_found") if observations else "no_vcs_root")
            confidence = "exact_vcs_url" if mapped else None
        elif node["kind"] == "bb_project":
            children = [edge["target"] for edge in outgoing[node["id"]] if edge["relation"] == "contains"]
            mapped = any(child in mapped_repositories for child in children)
            reason, confidence = ("repository_mapped", "inherited") if mapped else ("no_mapped_repositories", None)
        else:
            continue
        node["mappingStatus"], node["mappingReason"] = ("mapped" if mapped else "unmapped"), reason
        if confidence:
            node["mappingConfidence"] = confidence
        else:
            node.pop("mappingConfidence", None)


def coverage_report(nodes: list[dict]) -> dict:
    relevant = [node for node in nodes if node.get("kind") in MAPPING_KINDS]
    counts = {}
    for kind in sorted(MAPPING_KINDS):
        items = [node for node in relevant if node["kind"] == kind]
        mapped = sum(node.get("mappingStatus") == "mapped" for node in items)
        unknown = sum(node.get("mappingStatus") not in {"mapped", "unmapped"} for node in items)
        counts[kind] = {"total": len(items), "mapped": mapped, "unmapped": len(items) - mapped - unknown, "unknown": unknown}
    issues = [{key: node.get(key) for key in ("id", "kind", "label", "mappingReason")} for node in relevant if node["kind"] in {"repository", "build_configuration"} and node.get("mappingStatus") != "mapped"]
    total = counts["repository"]["total"] + counts["build_configuration"]["total"]
    mapped = counts["repository"]["mapped"] + counts["build_configuration"]["mapped"]
    return {"coveragePercent": round(mapped * 100 / total, 1) if total else None, "counts": counts, "issues": issues}
