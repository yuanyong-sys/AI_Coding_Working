from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.database import Database
from app.domains.demo.router import router as demo_router
from app.domains.demo.service import ensure_seeded
from app.domains.demo.schemas import DemoControlState


def create_app(
    *,
    database_url: str | None = None,
    legacy_json: Path | None = None,
    serve_frontend: bool = True,
) -> FastAPI:
    settings = Settings(database_url=database_url) if database_url else Settings()
    database = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.create_schema()
        async with database.sessions() as session:
            uses_default_database = database_url is None and "DRONE_POC_DATABASE_URL" not in os.environ
            migration_source = legacy_json if legacy_json is not None else (settings.legacy_json if uses_default_database else None)
            await ensure_seeded(session, legacy_json=migration_source)
        yield
        await database.close()

    app = FastAPI(title="无人机警务监控平台 POC API", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.demo_control = DemoControlState()
    app.include_router(demo_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if serve_frontend:
        _mount_frontend(app, settings.frontend_dist, settings.prototype_root)
    return app


def _mount_frontend(app: FastAPI, frontend_dist: Path, prototype_root: Path) -> None:
    prototype_pages = {"screen-overview.html", "dispatch-tasks.html", "alert-workbench.html", "stats-ledger.html"}
    prototype_assets = prototype_root / "assets"
    if prototype_assets.exists():
        app.mount("/prototype-assets", StaticFiles(directory=prototype_assets), name="prototype-assets")

    @app.get("/prototype/{page}", include_in_schema=False)
    async def prototype_page(page: str) -> FileResponse:
        if page not in prototype_pages:
            raise HTTPException(status_code=404, detail="PROTOTYPE_NOT_FOUND")
        return FileResponse(prototype_root / page)

    assets = frontend_dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/api/{route:path}", include_in_schema=False)
    async def unknown_api(route: str) -> None:
        raise HTTPException(status_code=404, detail="API_NOT_FOUND")

    @app.get("/{route:path}", include_in_schema=False)
    async def frontend_route(route: str) -> FileResponse:
        index = frontend_dist / "index.html"
        if not index.exists():
            return FileResponse(prototype_root / "index.html", status_code=503)
        return FileResponse(index)


app = create_app()
