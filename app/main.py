import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy import text
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import settings
from .layout import layered_positions
from .filters import filter_graph
from .models import Base, Edge, Node, Scan, SessionLocal, engine
from .service import refresh
from .subgraph import select_visible
from .web import PAGE
from .operations import prometheus_metrics, scan_duration_seconds

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    await refresh()
    scheduler.add_job(refresh, "interval", minutes=settings.refresh_minutes, id="refresh", max_instances=1, coalesce=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Artefact Graph", version="0.1.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index(): return PAGE


@app.get("/api/graph")
def graph(q: str = "", bb_project: str = "", tc_project: str = "", status_filter: str = "", engine_filter: str = "", has_image: bool | None = None, has_sbom: bool | None = None, target_only: bool = False, since_days: int = 0):
    with SessionLocal() as db:
        nodes = db.query(Node).all(); edges = db.query(Edge).all()
    result_nodes = [{"id": n.id, "kind": n.kind, "label": n.label, **json.loads(n.data)} for n in nodes]
    result_edges = [{"source": e.source, "target": e.target, "relation": e.relation, **json.loads(e.data)} for e in edges]
    result_nodes, result_edges = select_visible(result_nodes, result_edges, q)
    result_nodes, result_edges = filter_graph(
        result_nodes, result_edges, bb_project=bb_project, tc_project=tc_project,
        status=status_filter, engine=engine_filter, has_image=has_image, has_sbom=has_sbom, target_only=target_only,
        since_days=max(0, since_days),
    )
    return {"nodes": result_nodes, "edges": result_edges, "positions": layered_positions(result_nodes, result_edges)}


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
    return {"mode": settings.app_mode, "refreshMinutes": settings.refresh_minutes, "lastScan": None if not scan else {"status": scan.status, "message": scan.message, "finishedAt": scan.finished_at}}


@app.get("/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
def health_ready():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            scan = db.query(Scan).filter(Scan.status == "success").order_by(Scan.id.desc()).first()
        if not scan:
            raise HTTPException(503, "No successful scan yet")
        return {"status": "ready", "lastSuccessfulScan": scan.finished_at}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "Database unavailable")


@app.get("/api/scans")
def scan_history(limit: int = 20):
    with SessionLocal() as db:
        scans = db.query(Scan).order_by(Scan.id.desc()).limit(min(max(limit, 1), 100)).all()
    return [{"id": scan.id, "startedAt": scan.started_at, "finishedAt": scan.finished_at, "durationSeconds": scan_duration_seconds(scan), "status": scan.status, "message": scan.message} for scan in scans]


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    with SessionLocal() as db:
        scans = db.query(Scan).order_by(Scan.id.desc()).limit(settings.scan_history_limit).all()
        node_count, edge_count = db.query(Node).count(), db.query(Edge).count()
    return prometheus_metrics(scans, node_count, edge_count)
