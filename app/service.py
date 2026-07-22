import json
import re
import asyncio
import httpx
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .analyzer import find_pushes, publishes_sbom, resolve_teamcity_parameters
from .collectors import TeamCityCollector, repository_provider, teamcity_properties
from .config import settings
from .demo import dataset
from .models import Edge, Node, Scan, SessionLocal
from .source_analysis import build_source_paths, expand_scripts
from .registry import RegistryCollector


refresh_lock = asyncio.Lock()


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
            _save(nodes, edges)
            status, message = "success", f"{len(nodes)} nodes, {len(edges)} edges"
        except Exception as exc:
            status, message = "failed", str(exc)
            raise
        finally:
            with SessionLocal.begin() as db:
                row = db.get(Scan, scan.id)
                row.status, row.message, row.finished_at = status, message, datetime.now(timezone.utc)


async def collect_live():
    bb, tc = repository_provider(), TeamCityCollector(settings.teamcity_url, settings.teamcity_token)
    registry = RegistryCollector() if settings.registry_enabled else None
    manifest_cache = {}
    nodes, edges, repo_by_url, repo_records = [], [], {}, {}
    try:
        async for repo in bb.repositories():
            pid = f"bb-project:{repo.namespace}/{repo.project_key}"
            rid = f"repo:{repo.namespace}/{repo.slug}"
            nodes += [
                {"id": pid, "kind": "bb_project", "label": repo.project_name, "provider": repo.provider},
                {"id": rid, "kind": "repository", "label": repo.name, "url": repo.web_url, "provider": repo.provider},
            ]
            edges.append({"source": pid, "target": rid, "relation": "contains"})
            repo_records[rid] = repo
            for clone_url in repo.clone_urls:
                repo_by_url[normalize_url(clone_url)] = rid
        for summary in await tc.build_types():
            detail = await tc.build_type(summary["id"])
            bid, pid = f"build-type:{detail['id']}", f"tc-project:{detail['projectId']}"
            nodes += [{"id": pid, "kind": "tc_project", "label": detail["projectId"]}, {"id": bid, "kind": "build_configuration", "label": detail["name"], "url": detail.get("webUrl")}]
            edges.append({"source": pid, "target": bid, "relation": "contains"})
            roots = detail.get("vcs-root-entries", {}).get("vcs-root-entry", [])
            linked_repositories = []
            for entry in roots:
                props = {p["name"]: p.get("value", "") for p in entry.get("vcs-root", {}).get("properties", {}).get("property", [])}
                url = props.get("url", "")
                if repo_id := repo_by_url.get(normalize_url(url)):
                    edges.append({"source": repo_id, "target": bid, "relation": "built_by", "confidence": "exact_vcs_url"})
                    linked_repositories.append(repo_records[repo_id])
            scripts = []
            build_files = []
            parameters = teamcity_properties(detail, "parameters")
            for step in detail.get("steps", {}).get("step", []):
                props = {p["name"]: p.get("value", "") for p in step.get("properties", {}).get("property", [])}
                scripts.extend(resolve_teamcity_parameters(v, parameters) for k, v in props.items() if "script" in k.lower())
                build_files.extend(build_source_paths(step.get("type", ""), props))
            sources = await expand_scripts(bb, linked_repositories, scripts, build_files)
            pushed_images = []
            for source in sources:
                for push in find_pushes(source.text):
                    iid = f"image:{push['image']}"
                    if push["image"] not in manifest_cache:
                        manifest_cache[push["image"]] = await registry_manifest(registry, push["image"])
                    manifest = manifest_cache[push["image"]]
                    nodes.append({"id": iid, "kind": "container_image", "label": push["image"], "engine": push["engine"], **manifest})
                    edges.append({"source": bid, "target": iid, "relation": "pushes", "evidence": source.path})
                    pushed_images.append({"image": push["image"], "engine": push["engine"], "evidence": source.path, **manifest})
            artifact_rules = teamcity_properties(detail, "settings").get("artifactRules", "")
            if publishes_sbom(artifact_rules):
                aid = f"artifact:{bid}/sbom.json"
                nodes.append({"id": aid, "kind": "sbom", "label": "sbom.json", "rule": artifact_rules})
                edges.append({"source": bid, "target": aid, "relation": "publishes", "evidence": artifact_rules})
                for image in pushed_images:
                    edges.append({
                        "source": f"image:{image['image']}", "target": aid, "relation": "described_by",
                        "confidence": "same_build_configuration",
                    })
            for build in await tc.builds(detail["id"]):
                artifacts = await tc.artifacts(build["id"])
                node = build_node(build, pushed_images, artifacts)
                run_id = node["id"]
                nodes.append(node)
                edges.append({"source": bid, "target": run_id, "relation": "ran_as"})
            for dependency in detail.get("snapshot-dependencies", {}).get("snapshot-dependency", []):
                if source_id := dependency.get("source-buildType", {}).get("id"):
                    edges.append({"source": bid, "target": f"build-type:{source_id}", "relation": "snapshot_depends_on"})
            for dependency in detail.get("artifact-dependencies", {}).get("artifact-dependency", []):
                if source_id := dependency.get("source-buildType", {}).get("id"):
                    edges.append({"source": bid, "target": f"build-type:{source_id}", "relation": "uses_artifacts_from"})
        return deduplicate(nodes), deduplicate(edges, ("source", "target", "relation"))
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


def build_node(build: dict, pushed_images: list[dict], artifacts: list[dict]) -> dict:
    successful = build.get("status") == "SUCCESS"
    actual_images = pushed_images if successful else []
    return {
        "id": f"build:{build['id']}",
        "kind": "build",
        "label": f"#{build.get('number', build['id'])}",
        **{k: v for k, v in build.items() if k != "id"},
        "pushedImages": actual_images,
        "sbomArtifacts": [
            {**artifact, "relatedImages": [image.get("digest") or image["image"] for image in actual_images]}
            for artifact in artifacts if artifact.get("name", "").lower() == "sbom.json"
        ],
    }


async def registry_manifest(registry, image: str) -> dict:
    if registry is None:
        return {}
    try:
        return await registry.manifest(image) or {"registryStatus": "external"}
    except httpx.HTTPStatusError as exc:
        return {"registryStatus": "unavailable", "registryStatusCode": exc.response.status_code}
    except httpx.TransportError:
        return {"registryStatus": "unavailable"}
