import json
import re
import asyncio
import httpx
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .analyzer import find_executed_pushes, find_pushes, publishes_sbom, resolve_teamcity_parameters
from .collectors import TeamCityCollector, repository_provider, teamcity_properties
from .config import settings
from .demo import dataset
from .models import Edge, GraphSnapshot, Node, Scan, SessionLocal
from .source_analysis import build_source_paths, expand_scripts
from .registry import RegistryCollector
from .sbom import summarize_sbom
from .visual_states import build_visual_state
from .incremental import BoundedCache
from .snapshots import canonical_graph


refresh_lock = asyncio.Lock()
build_input_cache = BoundedCache(settings.incremental_cache_size)
source_cache = BoundedCache(settings.incremental_cache_size)


def _save(nodes, edges):
    with SessionLocal.begin() as db:
        node_ids = {item["id"] for item in nodes}
        edge_keys = {(item["source"], item["target"], item["relation"]) for item in edges}
        existing_nodes = {row.id: row for row in db.query(Node).all()}
        existing_edges = {(row.source, row.target, row.relation): row for row in db.query(Edge).all()}
        for item in nodes:
            core = {k: item[k] for k in ("id", "kind", "label")}
            data = {k: v for k, v in item.items() if k not in core}
            if row := existing_nodes.get(item["id"]):
                row.kind, row.label, row.data = core["kind"], core["label"], json.dumps(data)
            else:
                db.add(Node(**core, data=json.dumps(data)))
        for item in edges:
            core = {k: item[k] for k in ("source", "target", "relation")}
            data = {k: v for k, v in item.items() if k not in core}
            key = (core["source"], core["target"], core["relation"])
            if row := existing_edges.get(key):
                row.data = json.dumps(data)
            else:
                db.add(Edge(**core, data=json.dumps(data)))
        for node_id, row in existing_nodes.items():
            if node_id not in node_ids:
                db.delete(row)
        for key, row in existing_edges.items():
            if key not in edge_keys:
                db.delete(row)


async def refresh():
    async with refresh_lock:
        with SessionLocal.begin() as db:
            scan = Scan()
            db.add(scan)
        try:
            if settings.app_mode == "demo":
                nodes, edges = dataset()
            else:
                nodes, edges = await collect_live()
            annotate_visual_state(nodes, edges)
            _save(nodes, edges)
            _save_snapshot(scan.id, nodes, edges)
            status, message = "success", f"{len(nodes)} nodes, {len(edges)} edges"
        except Exception as exc:
            status, message = "failed", str(exc)
            raise
        finally:
            with SessionLocal.begin() as db:
                row = db.get(Scan, scan.id)
                row.status, row.message, row.finished_at = status, message, datetime.now(timezone.utc)
                old_ids = [item[0] for item in db.query(Scan.id).order_by(Scan.id.desc()).offset(settings.scan_history_limit).all()]
                if old_ids:
                    db.query(Scan).filter(Scan.id.in_(old_ids)).delete(synchronize_session=False)


def _save_snapshot(scan_id: int, nodes: list[dict], edges: list[dict]) -> None:
    payload, content_hash = canonical_graph(nodes, edges)
    with SessionLocal.begin() as db:
        db.add(GraphSnapshot(scan_id=scan_id, node_count=len(nodes), edge_count=len(edges), content_hash=content_hash, payload=payload))
        old_ids = [item[0] for item in db.query(GraphSnapshot.id).order_by(GraphSnapshot.id.desc()).offset(settings.snapshot_history_limit).all()]
        if old_ids:
            db.query(GraphSnapshot).filter(GraphSnapshot.id.in_(old_ids)).delete(synchronize_session=False)


async def collect_live():
    bb, tc = repository_provider(), TeamCityCollector(settings.teamcity_url, settings.teamcity_token)
    registry = RegistryCollector() if settings.registry_enabled else None
    manifest_cache = {}
    nodes, edges, repo_by_url, repo_records = [], [], {}, {}
    try:
        async for repo in bb.repositories():
            pid = f"bb-project:{repo.namespace}/{repo.project_key}"
            rid = f"repo:{repo.namespace}/{repo.slug}"
            nodes.append({"id": rid, "kind": "repository", "label": repo.name, "url": repo.web_url, "provider": repo.provider, "active": repo.active})
            if repo.project_key != "UNASSIGNED":
                nodes.append({"id": pid, "kind": "bb_project", "label": repo.project_name, "provider": repo.provider, "active": repo.active})
                edges.append({"source": pid, "target": rid, "relation": "contains"})
            repo_records[rid] = repo
            for clone_url in repo.clone_urls:
                repo_by_url[normalize_url(clone_url)] = rid
        for summary in await tc.build_types():
            try:
                detail = await tc.build_type(summary["id"])
            except httpx.HTTPError:
                continue
            if detail.get("projectId") in {"_Root", "Root"}:
                continue
            bid, pid = f"build-type:{detail['id']}", f"tc-project:{detail['projectId']}"
            nodes += [{"id": pid, "kind": "tc_project", "label": detail["projectId"], "active": True}, {"id": bid, "kind": "build_configuration", "label": detail["name"], "url": detail.get("webUrl"), "active": not detail.get("paused", False)}]
            edges.append({"source": pid, "target": bid, "relation": "contains"})
            roots = detail.get("vcs-root-entries", {}).get("vcs-root-entry", [])
            linked_repositories = []
            for entry in roots:
                props = {p["name"]: p.get("value", "") for p in entry.get("vcs-root", {}).get("properties", {}).get("property", [])}
                url = props.get("url", "")
                if repo_id := repo_by_url.get(normalize_url(url)):
                    edges.append({"source": repo_id, "target": pid, "relation": "maps_to", "confidence": "exact_vcs_url"})
                    linked_repositories.append(repo_records[repo_id])
            scripts = []
            build_files = []
            parameters = teamcity_properties(detail, "parameters")
            for step in detail.get("steps", {}).get("step", []):
                props = {p["name"]: p.get("value", "") for p in step.get("properties", {}).get("property", [])}
                scripts.extend(resolve_teamcity_parameters(v, parameters) for k, v in props.items() if "script" in k.lower())
                build_files.extend(build_source_paths(step.get("type", ""), props))
            source_key = (
                tuple(sorted((repo.provider, repo.namespace, repo.slug, repo.revision) for repo in linked_repositories)),
                tuple(sorted(scripts)), tuple(sorted(build_files)),
            )
            sources = source_cache.get(source_key)
            if sources is None:
                source_complete = True
                try:
                    sources = await expand_scripts(bb, linked_repositories, scripts, build_files)
                except httpx.HTTPError:
                    sources = []
                    source_complete = False
                if source_complete and all(repo.revision for repo in linked_repositories):
                    source_cache.put(source_key, sources)
            pushed_images = []
            for source in sources:
                for push in find_pushes(source.text):
                    if push["image"] not in manifest_cache:
                        manifest_cache[push["image"]] = await registry_manifest(registry, push["image"])
                    manifest = manifest_cache[push["image"]]
                    pushed_images.append({"image": push["image"], "engine": push["engine"], "evidence": source.path, **manifest})
            artifact_rules = teamcity_properties(detail, "settings").get("artifactRules", "")
            sbom_rule = artifact_rules if publishes_sbom(artifact_rules) else ""
            for build in await tc.builds(detail["id"]):
                finished = build.get("state", "finished") == "finished"
                cached = build_input_cache.get(str(build["id"])) if finished else None
                if cached is not None:
                    artifacts, build_log = cached
                else:
                    try:
                        artifacts = await tc.artifacts(build["id"]) if finished else []
                        for artifact in artifacts:
                            is_sbom = artifact.get("name", "").lower() == "sbom.json"
                            if is_sbom and artifact.get("contentHref"):
                                content, truncated = await tc.artifact_content(artifact["contentHref"])
                                artifact.update(summarize_sbom(content, truncated=truncated))
                            artifact.pop("contentHref", None)
                            if is_sbom:
                                artifact["artifactRule"] = sbom_rule
                        build_log = await tc.build_log(build["id"]) if finished else ""
                        if finished:
                            build_input_cache.put(str(build["id"]), (artifacts, build_log))
                    except httpx.HTTPError as exc:
                        artifacts, build_log = [], ""
                        build["collectionError"] = type(exc).__name__
                for artifact in artifacts:
                    if artifact.get("name", "").lower() == "sbom.json":
                        artifact["artifactRule"] = sbom_rule
                node = build_node(build, pushed_images, artifacts, build_log)
                run_id = node["id"]
                nodes.append(node)
                edges.append({"source": bid, "target": run_id, "relation": "ran_as"})
        nodes, edges = deduplicate(nodes), deduplicate(edges, ("source", "target", "relation"))
        annotate_visual_state(nodes, edges)
        return nodes, edges
    finally:
        await bb.close(); await tc.close()
        if registry:
            await registry.close()


def normalize_url(url: str):
    value = (url or "").strip().lower()
    if re.match(r"^[^/@]+@[^:]+:", value):
        user_host, path = value.split(":", 1)
        value = f"{user_host.split('@', 1)[1]}/{path}"
    elif "://" in value:
        parsed = urlsplit(value)
        value = f"{parsed.hostname or ''}{parsed.path}"
    value = value.rstrip("/").removesuffix(".git")
    return value.replace("/scm/", "/")


def deduplicate(items, keys=("id",)):
    result = {}
    for item in items:
        key = tuple(item[k] for k in keys)
        if key in result and (item.get("evidence") or result[key].get("evidence")):
            previous = result[key]
            evidence_paths = previous.get("evidencePaths", []) or [previous.get("evidence")]
            evidence_paths += item.get("evidencePaths", []) or [item.get("evidence")]
            result[key] = {**previous, **item, "evidencePaths": list(dict.fromkeys(path for path in evidence_paths if path))}
        else:
            result[key] = item
    return list(result.values())


def build_node(build: dict, pushed_images: list[dict], artifacts: list[dict], build_log: str = "") -> dict:
    successful = build.get("state", "finished") == "finished" and build.get("status") == "SUCCESS"
    configured = {(item.get("engine"), item["image"]): item for item in pushed_images}
    actual_images = [
        {**configured.get((item["engine"], item["image"]), {}), **item}
        for item in find_executed_pushes(build_log, pushed_images)
    ] if successful else []
    return {
        "id": f"build:{build['id']}",
        "kind": "build",
        "label": f"#{build.get('number', build['id'])}",
        **{k: v for k, v in build.items() if k != "id"},
        "pushedImages": actual_images,
        "hasImagePush": bool(actual_images),
        "sbomArtifacts": [
            {**artifact, "relatedImages": [image.get("digest") or image["image"] for image in actual_images]}
            for artifact in artifacts if artifact.get("name", "").lower() == "sbom.json"
        ],
        "hasSbom": any(artifact.get("name", "").lower() == "sbom.json" for artifact in artifacts),
    }


def annotate_visual_state(nodes: list[dict], edges: list[dict]) -> None:
    """Propagate actual push/SBOM presence upward through the product chain."""
    by_id = {node["id"]: node for node in nodes}
    incoming: dict[str, list[dict]] = {}
    for edge in edges:
        incoming.setdefault(edge["target"], []).append(edge)

    relevant = {
        node["id"] for node in nodes
        if node["kind"] == "build" and (node.get("hasImagePush") or node.get("hasSbom"))
    }
    stages = [
        ("build_configuration", "ran_as", "build"),
        ("tc_project", "contains", "build_configuration"),
        ("repository", "maps_to", "tc_project"),
        ("bb_project", "contains", "repository"),
    ]
    for parent_kind, relation, child_kind in stages:
        parents = {
            edge["source"] for child_id in tuple(relevant)
            for edge in incoming.get(child_id, [])
            if edge["relation"] == relation
            and by_id.get(edge["source"], {}).get("kind") == parent_kind
            and by_id.get(child_id, {}).get("kind") == child_kind
        }
        relevant.update(parents)

    for node in nodes:
        if node["kind"] in {"bb_project", "repository", "tc_project", "build_configuration"}:
            node.setdefault("active", True)
            node["hasTargetOutput"] = node["id"] in relevant
            node["visualReason"] = "inactive" if not node["active"] else "target_output_branch" if node["hasTargetOutput"] else "active"
        elif node["kind"] == "build":
            node["visualReason"] = build_visual_state(node)

    # Container activity is derived from the activity of direct children.
    for node in nodes:
        child_kind = {"tc_project": "build_configuration", "bb_project": "repository"}.get(node["kind"])
        if child_kind:
            children = [by_id[e["target"]] for e in edges if e["source"] == node["id"] and e["relation"] == "contains" and e["target"] in by_id and by_id[e["target"]]["kind"] == child_kind]
            if children:
                node["active"] = any(child.get("active", True) for child in children)


async def registry_manifest(registry, image: str) -> dict:
    if registry is None:
        return {}
    try:
        return await registry.manifest(image) or {"registryStatus": "external"}
    except httpx.HTTPStatusError as exc:
        return {"registryStatus": "unavailable", "registryStatusCode": exc.response.status_code}
    except httpx.TransportError:
        return {"registryStatus": "unavailable"}
