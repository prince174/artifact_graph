import json
import asyncio
import logging
from contextlib import suppress
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
from .snapshots import diff_graphs, snapshot_timeline
from .auth import AuthMiddleware, login, login_page, logout_response
from .mapping_quality import coverage_report
from .alerts import dispatch_webhooks
from .transport_security import validate_runtime_settings
from .security_headers import SecurityHeadersMiddleware
from .graph_analysis import graph_insights, impact_report
from .scheduling import schedule_refresh
from .graph_payload import compact_node
from .scan_progress import scan_progress
from .graph_queries import repository_search

scheduler = AsyncIOScheduler()


async def initial_refresh():
    try:
        await refresh()
    except Exception as exc:
        logging.getLogger(__name__).error("initial_refresh_failed error=%s", type(exc).__name__)


@asynccontextmanager
async def lifespan(app):
    from .logging_setup import configure_logging
    from .dry_run import fingerprint
    import logging
    configure_logging()
    logging.getLogger(__name__).info("startup fingerprint=%s log_level=%s projects=%r", fingerprint(), settings.log_level, settings.selected_bitbucket_projects or "ALL")
    validate_runtime_settings()
    schedule_refresh(scheduler, refresh)
    scheduler.add_job(dispatch_webhooks, "interval", minutes=1, id="webhook-outbox", max_instances=1, coalesce=True)
    scheduler.start()
    logging.getLogger(__name__).info("refresh_schedule daily=03:00 timezone=MSK(UTC+03:00) next_run=%s", scheduler.get_job("refresh").next_run_time)
    startup_task = asyncio.create_task(initial_refresh(), name="initial-refresh")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        startup_task.cancel()
        with suppress(asyncio.CancelledError):
            await startup_task


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


@app.get("/api/nodes/{node_id:path}")
def node_details(node_id: str):
    with SessionLocal() as db:
        row = db.get(Node, node_id)
        if row is None:
            raise HTTPException(404, "Node not found")
        return {"id": row.id, "kind": row.kind, "label": row.label, **json.loads(row.data)}


@app.get("/api/graph")
def graph(compact: bool = False, q: str = "", bb_project: str = "", tc_project: str = "", status_filter: str = "", engine_filter: str = "", has_image: bool | None = None, has_sbom: bool | None = None, target_only: bool = False, mapping_issues: bool = False, since_days: int = 0, cursor: str = "", limit: int = Query(10, ge=1, le=100), build_limit: int = Query(5, ge=1, le=20)):
    try:
        with SessionLocal() as db:
            # Build-dependent filters must inspect history before applying the UI limit.
            sql_build_limit = None if any((status_filter, engine_filter, has_image is not None, has_sbom is not None, since_days)) else build_limit
            selected = repository_search(db, q, cursor, limit, sql_build_limit) if q and not mapping_issues else None
            if selected is None:
                nodes = db.query(Node).all()
                edges = db.query(Edge).all()
                result_nodes = [{"id": n.id, "kind": n.kind, "label": n.label, **json.loads(n.data)} for n in nodes]
                result_edges = [{"source": e.source, "target": e.target, "relation": e.relation, **json.loads(e.data)} for e in edges]
            else:
                result_nodes, result_edges, pagination = selected
        if mapping_issues:
            result_nodes, result_edges = filter_graph(result_nodes, result_edges, mapping_issues=True)
            result_nodes, result_edges, pagination = paginate_mapping_issues(result_nodes, result_edges, cursor=cursor, limit=limit)
        else:
            if selected is None:
                result_nodes, result_edges, pagination = select_visible_page(result_nodes, result_edges, q, cursor=cursor, limit=limit)
            result_nodes, result_edges = filter_graph(
                result_nodes, result_edges, bb_project=bb_project, tc_project=tc_project,
                status=status_filter, engine=engine_filter, has_image=has_image, has_sbom=has_sbom, target_only=target_only,
                since_days=max(0, since_days),
            )
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    result_nodes, result_edges = limit_builds_per_configuration(result_nodes, result_edges, build_limit)
    return {"nodes": [compact_node(n) for n in result_nodes] if compact else result_nodes, "edges": result_edges, "positions": layered_positions(result_nodes, result_edges), "pagination": pagination}


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


@app.get("/api/insights")
def insights():
    with SessionLocal() as db:
        nodes = [{"id": n.id, "kind": n.kind, "label": n.label, **json.loads(n.data)} for n in db.query(Node).all()]
        edges = [{"source": e.source, "target": e.target, "relation": e.relation, **json.loads(e.data)} for e in db.query(Edge).all()]
    return graph_insights(nodes, edges)


@app.get("/api/impact/{node_id:path}")
def impact(node_id: str):
    with SessionLocal() as db:
        nodes = [{"id": n.id, "kind": n.kind, "label": n.label, **json.loads(n.data)} for n in db.query(Node).all()]
        edges = [{"source": e.source, "target": e.target, "relation": e.relation, **json.loads(e.data)} for e in db.query(Edge).all()]
    try:
        return impact_report(nodes, edges, node_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


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
    job = scheduler.get_job("refresh")
    return {"mode": settings.app_mode, "refreshMinutes": 1440,
            "progress": scan_progress.snapshot(),
            "refreshSchedule": {"time": "03:00", "timezone": "MSK", "utcOffset": "+03:00",
                                "nextRun": job.next_run_time.isoformat() if job and getattr(job, "next_run_time", None) else None},
            "lastScan": None if not scan else {"status": scan.status, "message": scan.message, "finishedAt": scan.finished_at, "upstream": json.loads(scan.details or "{}")}}


@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            scan = db.query(Scan).filter(Scan.status.in_(("success", "degraded"))).order_by(Scan.id.desc()).first()
        return {"status": "ready", "graphReady": scan is not None,
                "lastUsableScan": scan.finished_at if scan else None,
                "degraded": scan.status == "degraded" if scan else False}
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


@app.get("/api/timeline")
def timeline(limit: int = Query(20, ge=2, le=100)):
    with SessionLocal() as db:
        rows = db.query(GraphSnapshot).order_by(GraphSnapshot.id.desc()).limit(limit).all()
    return snapshot_timeline([
        {"id": row.id, "scanId": row.scan_id, "createdAt": row.created_at, "nodeCount": row.node_count, "edgeCount": row.edge_count, "hash": row.content_hash, "graph": json.loads(row.payload)}
        for row in rows
    ])


@app.get("/api/snapshots/{snapshot_id}/graph")
def snapshot_graph(snapshot_id: int):
    with SessionLocal() as db:
        row = db.get(GraphSnapshot, snapshot_id)
    if not row:
        raise HTTPException(404, "Snapshot not found")
    graph = json.loads(row.payload)
    return {**graph, "positions": layered_positions(graph.get("nodes", []), graph.get("edges", [])), "snapshot": {"id": row.id, "createdAt": row.created_at}}


@app.get("/api/snapshots/{before_id}/diff/{after_id}")
def snapshot_diff(before_id: int, after_id: int):
    with SessionLocal() as db:
        before, after = db.get(GraphSnapshot, before_id), db.get(GraphSnapshot, after_id)
    if not before or not after:
        raise HTTPException(404, "Snapshot not found")
    return {"before": before_id, "after": after_id, **diff_graphs(json.loads(before.payload), json.loads(after.payload))}
