import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
async def client(tmp_path: Path):
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path / 'poc.db'}", serve_frontend=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
            yield value


@pytest.mark.asyncio
async def test_state_uses_sqlite_and_exposes_dashboard_data(client: AsyncClient):
    response = await client.get("/api/state")
    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshotVersion"] == "POC-DEMO-V1"
    assert payload["schemaVersion"] == 2
    assert len(payload["drones"]) == 8
    assert len(payload["tasks"]) == 5
    assert len(payload["alerts"]) == 7
    assert payload["ledgers"]


@pytest.mark.asyncio
async def test_business_change_survives_new_app_instance(tmp_path: Path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'persistent.db'}"
    first = create_app(database_url=database_url, serve_frontend=False)
    async with first.router.lifespan_context(first):
        async with AsyncClient(transport=ASGITransport(app=first), base_url="http://test") as client:
            changed = await client.patch("/api/tasks/RW-20260905-012", json={"status": "DISPATCHED"})
            assert changed.status_code == 200

    second = create_app(database_url=database_url, serve_frontend=False)
    async with second.router.lifespan_context(second):
        async with AsyncClient(transport=ASGITransport(app=second), base_url="http://test") as client:
            state = (await client.get("/api/state")).json()
            task = next(item for item in state["tasks"] if item["id"] == "RW-20260905-012")
            assert task["status"] == "DISPATCHED"


@pytest.mark.asyncio
async def test_reset_requires_confirmation_is_repeatable_and_audited(client: AsyncClient):
    rejected = await client.post("/api/demo/reset", json={"confirmed": False})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "CONFIRMATION_REQUIRED"

    first = (await client.post("/api/demo/reset", json={"confirmed": True})).json()
    second = (await client.post("/api/demo/reset", json={"confirmed": True})).json()
    assert first["businessState"] == second["businessState"]
    assert first["snapshotVersion"] == "POC-DEMO-V1"
    audit = (await client.get("/api/audit")).json()["audit"]
    assert [entry["action"] for entry in audit[-2:]] == ["DEMO_RESET", "DEMO_RESET"]


@pytest.mark.asyncio
async def test_legacy_json_is_imported_once_with_changes_and_audit(tmp_path: Path):
    legacy = tmp_path / "poc.json"
    document = {
        "schemaVersion": 2,
        "drones": [],
        "tasks": [{"id": "LEGACY-1", "name": "保留任务", "status": "DISPATCHED", "progress": 9, "overdue": False}],
        "alerts": [],
        "ledgers": [],
        "audit": [{"action": "DEMO_RESET", "snapshotVersion": "POC-DEMO-V1", "occurredAt": "2026-09-07T00:00:00Z"}],
    }
    legacy.write_text(json.dumps(document), encoding="utf-8")
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}"
    app = create_app(database_url=database_url, legacy_json=legacy, serve_frontend=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            state = (await client.get("/api/state")).json()
            assert state["tasks"][0]["name"] == "保留任务"
            assert len((await client.get("/api/audit")).json()["audit"]) == 1


@pytest.mark.asyncio
async def test_unknown_api_returns_json_404(tmp_path: Path):
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/misspelled")
            assert response.status_code == 404
            assert response.json()["detail"] == "API_NOT_FOUND"
