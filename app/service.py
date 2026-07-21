import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .analyzer import find_pushes, publishes_sbom
from .collectors import TeamCityCollector, repository_provider
from .config import settings
from .demo import dataset
from .models import Edge, Node, Scan, SessionLocal
from .source_analysis import expand_scripts


def _save(nodes, edges):
    with SessionLocal.begin() as db:
        db.query(Edge).delete()
        db.query(Node).delete()
        for item in nodes:
            core = {k: item[k] for k in ("id", "kind", "label")}
            data = {k: v for k, v in item.items() if k not in core}
            db.add(Node(**core, data=json.dumps(data)))
        for item in edges:
            core = {k: item[k] for k in ("source", "target", "relation")}
            data = {k: v for k, v in item.items() if k not in core}
            db.add(Edge(**core, data=json.dumps(data)))


async def refresh():
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
            for step in detail.get("steps", {}).get("step", []):
                props = {p["name"]: p.get("value", "") for p in step.get("properties", {}).get("property", [])}
                scripts.extend(v for k, v in props.items() if "script" in k.lower())
            sources = await expand_scripts(bb, linked_repositories, scripts)
            for source in sources:
                for push in find_pushes(source.text):
                    iid = f"image:{push['image']}"
                    nodes.append({"id": iid, "kind": "container_image", "label": push["image"], "engine": push["engine"]})
                    edges.append({"source": bid, "target": iid, "relation": "pushes", "evidence": source.path})
            if publishes_sbom(detail.get("artifactRules", "")):
                aid = f"artifact:{bid}/sbom.json"
                nodes.append({"id": aid, "kind": "sbom", "label": "sbom.json"})
                edges.append({"source": bid, "target": aid, "relation": "publishes"})
            for build in await tc.builds(detail["id"]):
                run_id = f"build:{build['id']}"
                build_data = {k: v for k, v in build.items() if k not in {"id", "label", "kind"}}
                nodes.append({"id": run_id, "kind": "build", "label": f"#{build.get('number', build['id'])}", **build_data})
                edges.append({"source": bid, "target": run_id, "relation": "ran_as"})
        return deduplicate(nodes), deduplicate(edges, ("source", "target", "relation"))
    finally:
        await bb.close(); await tc.close()


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
    return list({tuple(item[k] for k in keys): item for item in items}.values())
