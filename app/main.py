import json
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import settings
from .layout import layered_positions
from .filters import filter_graph
from .models import Edge, GraphSnapshot, Node, Scan, SessionLocal, WebhookDelivery
from .service import build_input_cache, refresh, source_cache
from .subgraph import limit_builds_per_configuration, paginate_mapping_issues, select_visible_page
from .web import PAGE
from .version import __version__
from .operations import prometheus_metrics, scan_duration_seconds
from .provider_metrics import provider_metrics
from .snapshots import diff_graphs
from .auth import AuthMiddleware, login, login_page, logout_response
from .mapping_quality import coverage_report
from .alerts import dispatch_webhooks
from .transport_security import validate_runtime_settings
from .security_headers import SecurityHeadersMiddleware

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app):
    validate_runtime_settings()
    await refresh()
    scheduler.add_job(refresh, "interval", minutes=settings.refresh_minutes, id="refresh", max_instances=1, coalesce=True)
    scheduler.add_job(dispatch_webhooks, "interval", minutes=1, id="webhook-outbox", max_instances=1, coalesce=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Artefact Graph", version=__version__, lifespan=lifespan)
app.add_middleware(AuthMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static"), check_dir=False), name="static")


@app.get("/login", response_class=HTMLResponse)
def login_form(): return login_page()


@app.post("/login")
async def login_submit(request: Request): return await login(request)


@app.get("/api/session")
def session(request: Request): return {"user": request.state.session["user"], "csrf": request.state.session["csrf"]}


@app.post("/api/logout")
def logout(): return logout_response()


@app.get("/", response_class=HTMLResponse)
def index(): return PAGE


@app.get("/api/version")
def version(): return {"version": __version__}


@app.get("/api/graph")
def graph(q: str = "", bb_project: str = "", tc_project: str = "", status_filter: str = "", engine_filter: str = "", has_image: bool | None = None, has_sbom: bool | None = None, target_only: bool = False, mapping_issues: bool = False, since_days: int = 0, cursor: str = "", limit: int = Query(10, ge=1, le=100), build_limit: int = Query(5, ge=1, le=20)):
    with SessionLocal() as db:
        nodes = db.query(Node).all(); edges = db.query(Edge).all()
    result_nodes = [{"id": n.id, "kind": n.kind, "label": n.label, **json.loads(n.data)} for n in nodes]
    result_edges = [{"source": e.source, "target": e.target, "relation": e.relation, **json.loads(e.data)} for e in edges]
    try:
        if mapping_issues:
            result_nodes, result_edges = filter_graph(result_nodes, result_edges, mapping_issues=True)
            result_nodes, result_edges, pagination = paginate_mapping_issues(result_nodes, result_edges, cursor=cursor, limit=limit)
        else:
            result_nodes, result_edges, pagination = select_visible_page(result_nodes, result_edges, q, cursor=cursor, limit=limit)
            result_nodes, result_edges = filter_graph(
                result_nodes, result_edges, bb_project=bb_project, tc_project=tc_project,
                status=status_filter, engine=engine_filter, has_image=has_image, has_sbom=has_sbom, target_only=target_only,
                since_days=max(0, since_days),
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    result_nodes, result_edges = limit_builds_per_configuration(result_nodes, result_edges, build_limit)
    return {"nodes": result_nodes, "edges": result_edges, "positions": layered_positions(result_nodes, result_edges), "pagination": pagination}


@app.get("/api/options")
def options():
    with SessionLocal() as db:
        rows = db.query(Node).filter(Node.kind.in_(("bb_project", "tc_project"))).order_by(Node.kind, Node.label, Node.id).all()
    return {"bbProjects": [{"id": row.id, "label": row.label} for row in rows if row.kind == "bb_project"], "tcProjects": [{"id": row.id, "label": row.label} for row in rows if row.kind == "tc_project"]}


@app.get("/api/coverage")
def coverage(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)):
    with SessionLocal() as db:
        nodes = [{"id": n.id, "kind": n.kind, "label": n.label, **json.loads(n.data)} for n in db.query(Node).all()]
    report = coverage_report(nodes)
    issues = report["issues"]
    report["issues"] = issues[offset:offset + limit]
    report["issuePagination"] = {"offset": offset, "limit": limit, "total": len(issues), "hasMore": offset + limit < len(issues)}
    return report


@app.post("/api/refresh", status_code=202)
async def manual_refresh():
    if scheduler.get_job("manual-refresh"):
        raise HTTPException(409, "Refresh already queued")
    scheduler.add_job(refresh, id="manual-refresh")
    return {"status": "queued"}


@app.get("/api/status")
def status():
    with SessionLocal() as db:
        scan = db.query(Scan).order_by(Scan.id.desc()).first()
    return {"mode": settings.app_mode, "refreshMinutes": settings.refresh_minutes, "lastScan": None if not scan else {"status": scan.status, "message": scan.message, "finishedAt": scan.finished_at, "upstream": json.loads(scan.details or "{}")}}


@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            scan = db.query(Scan).filter(Scan.status.in_(("success", "degraded"))).order_by(Scan.id.desc()).first()
        if not scan:
            raise HTTPException(503, "No successful scan yet")
        return {"status": "ready", "lastUsableScan": scan.finished_at, "degraded": scan.status == "degraded"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Database unavailable")


@app.get("/api/scans")
def scan_history(limit: int = 20):
    with SessionLocal() as db:
        scans = db.query(Scan).order_by(Scan.id.desc()).limit(min(max(limit, 1), 100)).all()
    return [{"id": scan.id, "startedAt": scan.started_at, "finishedAt": scan.finished_at, "durationSeconds": scan_duration_seconds(scan), "status": scan.status, "message": scan.message, "upstream": json.loads(scan.details or "{}")} for scan in scans]


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    with SessionLocal() as db:
        scans = db.query(Scan).order_by(Scan.id.desc()).limit(settings.scan_history_limit).all()
        node_count, edge_count = db.query(Node).count(), db.query(Edge).count()
        webhook_pending = db.query(WebhookDelivery).filter(WebhookDelivery.status == "pending").count()
        webhook_dead = db.query(WebhookDelivery).filter(WebhookDelivery.status == "dead").count()
    base = prometheus_metrics(scans, node_count, edge_count)
    return base + "\n".join([
        "# TYPE artifact_graph_incremental_cache_hits_total counter",
        f'artifact_graph_incremental_cache_hits_total{{cache="build"}} {build_input_cache.hits}',
        f'artifact_graph_incremental_cache_hits_total{{cache="source"}} {source_cache.hits}',
        "# TYPE artifact_graph_incremental_cache_misses_total counter",
        f'artifact_graph_incremental_cache_misses_total{{cache="build"}} {build_input_cache.misses}',
        f'artifact_graph_incremental_cache_misses_total{{cache="source"}} {source_cache.misses}',
        "",
        "# TYPE artifact_graph_webhook_deliveries gauge",
        f'artifact_graph_webhook_deliveries{{status="pending"}} {webhook_pending}',
        f'artifact_graph_webhook_deliveries{{status="dead"}} {webhook_dead}',
        "",
    ]) + provider_metrics.prometheus()


@app.get("/api/snapshots")
def snapshots(limit: int = 20):
    with SessionLocal() as db:
        rows = db.query(GraphSnapshot).order_by(GraphSnapshot.id.desc()).limit(min(max(limit, 1), 100)).all()
    return [{"id": row.id, "scanId": row.scan_id, "createdAt": row.created_at, "nodeCount": row.node_count, "edgeCount": row.edge_count, "hash": row.content_hash} for row in rows]


@app.get("/api/snapshots/{before_id}/diff/{after_id}")
def snapshot_diff(before_id: int, after_id: int):
    with SessionLocal() as db:
        before, after = db.get(GraphSnapshot, before_id), db.get(GraphSnapshot, after_id)
    if not before or not after:
        raise HTTPException(404, "Snapshot not found")
    return {"before": before_id, "after": after_id, **diff_graphs(json.loads(before.payload), json.loads(after.payload))}
