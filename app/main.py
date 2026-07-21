import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from .config import settings
from .layout import layered_positions
from .models import Base, Edge, Node, Scan, SessionLocal, engine
from .service import refresh
from .subgraph import select_visible
from .web import PAGE

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
def graph(q: str = ""):
    with SessionLocal() as db:
        nodes = db.query(Node).all(); edges = db.query(Edge).all()
    result_nodes = [{"id": n.id, "kind": n.kind, "label": n.label, **json.loads(n.data)} for n in nodes]
    result_edges = [{"source": e.source, "target": e.target, "relation": e.relation, **json.loads(e.data)} for e in edges]
    result_nodes, result_edges = select_visible(result_nodes, result_edges, q)
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
