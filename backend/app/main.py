from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    ai,
    alert_rules,
    audit_logs,
    auth,
    call_logs,
    executions,
    incidents,
    interfaces,
    monitoring,
    performance,
    sla,
    users,
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

app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(interfaces.router, prefix="/api")
app.include_router(executions.router, prefix="/api")
app.include_router(call_logs.router, prefix="/api")
app.include_router(incidents.router, prefix="/api")
app.include_router(sla.router, prefix="/api")
app.include_router(performance.router, prefix="/api")
app.include_router(audit_logs.router, prefix="/api")
app.include_router(alert_rules.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(monitoring.router)  # /ws/...

# 문서 (개발/기획) 정적 서빙 — 로그인 페이지에서 링크로 노출.
# docker-compose 볼륨 `./file:/docs:ro` 로 마운트. 컨테이너 밖에서 실행 시
# 리포 루트의 file/ 폴더를 사용.
_DOCS_DIR = "/docs" if os.path.isdir("/docs") else os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "file")
)
if os.path.isdir(_DOCS_DIR):
    app.mount("/api/docs-file", StaticFiles(directory=_DOCS_DIR), name="docs-file")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
