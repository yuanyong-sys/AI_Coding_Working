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
            changed = await client.patch("/api/tasks/RW-20260905-007", json={"status": "PENDING_EXECUTION"})
            assert changed.status_code == 200

    second = create_app(database_url=database_url, serve_frontend=False)
    async with second.router.lifespan_context(second):
        async with AsyncClient(transport=ASGITransport(app=second), base_url="http://test") as client:
            state = (await client.get("/api/state")).json()
            task = next(item for item in state["tasks"] if item["id"] == "RW-20260905-007")
            assert task["status"] == "PENDING_EXECUTION"


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
            assert state["tasks"][0]["status"] == "PENDING_EXECUTION"
            assert len((await client.get("/api/audit")).json()["audit"]) == 1


@pytest.mark.asyncio
async def test_unknown_api_returns_json_404(tmp_path: Path):
    database_path = tmp_path / "missing" / "nested" / "api.db"
    app = create_app(database_url=f"sqlite+aiosqlite:///{database_path}")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/misspelled")
            assert response.status_code == 404
            assert response.json()["detail"] == "API_NOT_FOUND"
    assert database_path.exists()


@pytest.mark.asyncio
async def test_task_validation_reports_all_blockers(client: AsyncClient):
    response = await client.post("/api/tasks/validate", json={
        "name": "冲突任务", "date": "2026-09-08", "start": "10:00", "end": "09:00", "droneId": "U-08",
        "battery": 18, "route": "演示禁飞区航线", "aiItems": []
    })
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()["blockers"]}
    assert codes == {"TIME_INVALID", "LOW_BATTERY", "AIRSPACE_CONFLICT", "AI_REQUIRED"}
    assert response.json()["canDispatch"] is False


@pytest.mark.asyncio
async def test_create_dispatch_failure_retry_and_batch(client: AsyncClient):
    created = await client.post("/api/tasks", json={
        "name": "AC03 合法巡检", "date": "2026-09-08", "start": "15:00", "end": "16:00", "droneId": "U-01",
        "battery": 82, "route": "贵北高速巡检线", "aiItems": ["交通事故"]
    })
    assert created.status_code == 201
    task = created.json()
    assert task["status"] == "PENDING_DISPATCH"

    failed = await client.post(f"/api/tasks/{task['id']}/dispatch", json={"simulateFailure": True})
    assert failed.status_code == 503
    state = (await client.get("/api/state")).json()
    assert next(item for item in state["tasks"] if item["id"] == task["id"])["status"] == "PENDING_DISPATCH"

    retried = await client.post(f"/api/tasks/{task['id']}/dispatch", json={"simulateFailure": False})
    assert retried.status_code == 200
    assert retried.json()["status"] == "PENDING_EXECUTION"

    batch = await client.post("/api/tasks/dispatch", json={"taskIds": ["RW-20260905-007", "RW-20260905-018"]})
    assert batch.status_code == 200
    assert batch.json()["successCount"] == 2

    missing = await client.post("/api/tasks/dispatch", json={"taskIds": ["TYPO-MISSING"]})
    assert missing.status_code == 200
    assert missing.json() == {"successCount": 0, "failedCount": 1, "failedIds": ["TYPO-MISSING"]}


@pytest.mark.asyncio
async def test_task_fields_conflicts_and_state_machine_are_server_owned(client: AsyncClient):
    low_battery = await client.post("/api/tasks/validate", json={
        "name": "伪造电量", "date": "2026-09-08", "start": "15:00", "end": "16:00", "droneId": "U-08",
        "battery": 100, "route": "贵北高速巡检线", "aiItems": ["交通事故"]
    })
    assert "LOW_BATTERY" in {item["code"] for item in low_battery.json()["blockers"]}

    first = await client.post("/api/tasks", json={
        "name": "第一任务", "date": "2026-09-08", "start": "15:00", "end": "16:00", "droneId": "U-01",
        "battery": 1, "route": "贵北高速巡检线", "aiItems": ["交通事故"]
    })
    assert first.status_code == 201
    stored = first.json()
    assert stored["droneId"] == "U-01"
    assert stored["route"] == "贵北高速巡检线"
    assert stored["aiItems"] == ["交通事故"]

    overlap = await client.post("/api/tasks/validate", json={
        "name": "重叠任务", "date": "2026-09-08", "start": "15:30", "end": "16:30", "droneId": "警航-01",
        "battery": 100, "route": "人民路沿线", "aiItems": ["车辆违停"]
    })
    assert "SCHEDULE_CONFLICT" in {item["code"] for item in overlap.json()["blockers"]}

    invalid = await client.post(f"/api/tasks/{stored['id']}/transition", json={"target": "COMPLETED"})
    assert invalid.status_code == 409
    for target in ["PENDING_EXECUTION", "RUNNING", "TERMINATED"]:
        changed = await client.post(f"/api/tasks/{stored['id']}/transition", json={"target": target})
        assert changed.status_code == 200
        assert changed.json()["status"] == target


@pytest.mark.asyncio
async def test_running_mission_monitor_and_controls_are_audited(client: AsyncClient):
    seeded_control = await client.post("/api/tasks/RW-20260905-012/control", json={"command": "HOVER"})
    assert seeded_control.status_code == 200
    for target in ["PENDING_EXECUTION", "RUNNING"]:
        changed = await client.post("/api/tasks/RW-20260905-020/transition", json={"target": target})
        assert changed.status_code == 200

    monitor = await client.get("/api/tasks/RW-20260905-020/monitor")
    assert monitor.status_code == 200
    assert monitor.json()["video"] == {"mode": "SIMULATED", "label": "视频流占位"}
    assert set(monitor.json()["telemetry"]) == {"altitudeM", "speedMps", "batteryPct", "signalDbm"}
    assert len(monitor.json()["trajectory"]) >= 3

    sent = await client.post("/api/tasks/RW-20260905-020/control", json={"command": "HOVER"})
    assert sent.status_code == 200
    assert sent.json()["result"] == "SUCCESS"
    assert sent.json()["simulated"] is True

    failed = await client.post(
        "/api/tasks/RW-20260905-020/control",
        json={"command": "RETURN", "simulateFailure": True},
    )
    assert failed.status_code == 503
    audit = (await client.get("/api/audit")).json()["audit"]
    assert [(item["action"], item["result"]) for item in audit[-2:]] == [
        ("MISSION_CONTROL_HOVER", "SUCCESS"),
        ("MISSION_CONTROL_RETURN", "FAILED"),
    ]
    assert audit[-1]["actor"] == "王警官"
    assert audit[-1]["beforeState"] == "RUNNING"
    assert audit[-1]["afterState"] == "RUNNING"
    assert audit[-1]["failureReason"] == "模拟指令失败"


@pytest.mark.asyncio
async def test_anomaly_auto_switches_backup_or_marks_abnormal_with_unique_alert(client: AsyncClient):
    created = (await client.post("/api/tasks", json={
        "name": "备用机续飞", "date": "2026-09-09", "start": "15:00", "end": "16:00",
        "droneId": "U-01", "battery": 82, "route": "贵北高速巡检线",
        "aiItems": ["交通事故"], "backupDrone": "U-02",
    })).json()
    for target in ["PENDING_EXECUTION", "RUNNING"]:
        await client.post(f"/api/tasks/{created['id']}/transition", json={"target": target})

    switched = await client.post(f"/api/tasks/{created['id']}/anomaly", json={"kind": "LINK_LOSS"})
    assert switched.status_code == 200
    assert switched.json()["mission"]["status"] == "RUNNING"
    assert switched.json()["mission"]["droneId"] == "U-02"
    assert switched.json()["outcome"] == "BACKUP_SWITCHED"
    alert_id = switched.json()["alert"]["id"]

    repeated = await client.post(f"/api/tasks/{created['id']}/anomaly", json={"kind": "LOW_BATTERY"})
    assert repeated.json()["alert"]["id"] == alert_id
    state = (await client.get("/api/state")).json()
    assert len([item for item in state["alerts"] if item.get("missionId") == created["id"]]) == 1

    abnormal = await client.post("/api/tasks/RW-20260905-015/anomaly", json={"kind": "LINK_LOSS"})
    assert abnormal.json()["outcome"] == "ABNORMAL"
    assert abnormal.json()["mission"]["status"] == "ABNORMAL"
    audit = (await client.get("/api/audit")).json()["audit"]
    assert any(item["action"] == "MISSION_AUTO_BACKUP_SWITCH" for item in audit)
    assert any(item["action"] == "MISSION_MARKED_ABNORMAL" for item in audit)
