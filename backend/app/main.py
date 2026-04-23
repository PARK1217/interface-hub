from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    ai,
    call_logs,
    executions,
    incidents,
    interfaces,
    monitoring,
    performance,
    sla,
)
from app.core.config import get_settings
from app.core.database import init_db
from app.services.scheduler import start_scheduler, stop_scheduler

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
log = logging.getLogger("noahub")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    log.info("noahub starting up — env=%s", settings.app_env)
    init_db()
    start_scheduler()
    try:
        yield
    finally:
        stop_scheduler()
        log.info("noahub shut down")


app = FastAPI(
    title="NOA Interface Hub",
    description="보험사 외부 인터페이스 통합 관제 플랫폼",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(interfaces.router, prefix="/api")
app.include_router(executions.router, prefix="/api")
app.include_router(call_logs.router, prefix="/api")
app.include_router(incidents.router, prefix="/api")
app.include_router(sla.router, prefix="/api")
app.include_router(performance.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(monitoring.router)  # /ws/...


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
