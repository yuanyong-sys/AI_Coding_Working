import json
from io import BytesIO
from zipfile import ZipFile
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
async def test_public_demo_control_advances_clock_and_injects_retriable_failures(client: AsyncClient):
    initial = (await client.get("/api/demo/control")).json()
    assert initial == {
        "simulationClock": "2026-09-05T14:32:00+08:00",
        "failures": {"data": False, "map": False, "media": False, "control": False},
    }

    advanced = await client.post("/api/demo/control", json={
        "advanceMinutes": 7,
        "failures": ["data", "map", "media", "control"],
    })
    assert advanced.status_code == 200
    assert advanced.json() == {
        "simulationClock": "2026-09-05T14:39:00+08:00",
        "failures": {"data": True, "map": True, "media": True, "control": True},
    }

    failed_state = await client.get("/api/state")
    assert failed_state.status_code == 503
    assert failed_state.json()["detail"] == {
        "code": "DEMO_DATA_UNAVAILABLE", "reason": "演示数据服务加载失败", "retry": "/api/demo/control/retry/data"
        , "lastValid": "2026-09-05T14:39:00+08:00"
    }
    retried = await client.post("/api/demo/control/retry/data")
    assert retried.status_code == 200
    assert retried.json()["failures"]["data"] is False
    restored_state = await client.get("/api/state")
    assert restored_state.status_code == 200
    assert restored_state.json()["simulationClock"] == "2026-09-05T14:39:00+08:00"
    audit = (await client.get("/api/audit")).json()["audit"]
    assert any(item["action"] == "ALERT_EMERGENCY_REMINDER" for item in audit)


@pytest.mark.asyncio
async def test_demo_failures_report_reason_last_valid_information_and_retry(client: AsyncClient):
    await client.post("/api/demo/control", json={"failures": ["map", "media", "control"]})

    map_status = await client.get("/api/map/status")
    assert map_status.status_code == 503
    assert map_status.json()["detail"] == {
        "code": "DEMO_MAP_UNAVAILABLE", "reason": "地图底图服务不可用",
        "lastValid": "2026-09-05T14:32:00+08:00", "retry": "/api/demo/control/retry/map",
    }

    evidence = await client.get("/api/alerts/GJ-20260905-031/evidence")
    assert evidence.status_code == 503
    assert evidence.json()["detail"]["code"] == "DEMO_MEDIA_STREAM_INTERRUPTED"
    assert evidence.json()["detail"]["lastValid"] == "2026-09-05T14:32:00+08:00"

    control = await client.post("/api/tasks/RW-20260905-012/control", json={"command": "HOVER"})
    assert control.status_code == 504
    assert control.json()["detail"]["code"] == "DEMO_CONTROL_TIMEOUT"
    state = (await client.post("/api/demo/control/retry/control")).json()
    assert state["failures"]["control"] is False
    assert (await client.post("/api/tasks/RW-20260905-012/control", json={"command": "HOVER"})).status_code == 200


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
    audit = (await client.get("/api/audit")).json()["audit"]
    assert any(
        item.get("subjectId") == stored["id"]
        and item["action"] == "MISSION_DEMO_DATA_ARCHIVED"
        and item["result"] == "SUCCESS"
        for item in audit
    )


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
    assert audit[-2]["beforeState"] is None
    assert audit[-2]["afterState"] == "HOVER"
    assert audit[-1]["beforeState"] == "HOVER"
    assert audit[-1]["afterState"] == "HOVER"
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
    assert repeated.json()["outcome"] == "ABNORMAL"
    assert repeated.json()["mission"]["status"] == "ABNORMAL"
    state = (await client.get("/api/state")).json()
    assert len([item for item in state["alerts"] if item.get("missionId") == created["id"]]) == 1

    abnormal = await client.post("/api/tasks/RW-20260905-015/anomaly", json={"kind": "LINK_LOSS"})
    assert abnormal.json()["outcome"] == "ABNORMAL"
    assert abnormal.json()["mission"]["status"] == "ABNORMAL"
    audit = (await client.get("/api/audit")).json()["audit"]
    assert any(item["action"] == "MISSION_AUTO_BACKUP_SWITCH" for item in audit)
    assert any(item["action"] == "MISSION_MARKED_ABNORMAL" for item in audit)


@pytest.mark.asyncio
async def test_alert_queue_filters_and_exposes_complete_detail_and_evidence(client: AsyncClient):
    filtered = await client.get("/api/alerts", params={"level": "EMERGENCY", "time": "30m", "keyword": "K1582"})
    assert filtered.status_code == 200
    assert [item["id"] for item in filtered.json()["alerts"]] == ["GJ-20260905-031"]

    detail = await client.get("/api/alerts/GJ-20260905-031")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["alert"]["location"] == "兰海高速 K1582 都匀段"
    assert payload["relatedMission"]["id"] == "RW-20260905-012"
    assert payload["relatedMission"]["droneId"] == "U-03"
    assert payload["relatedMission"]["route"] == "兰海高速都匀段"
    assert len(payload["evidence"]["frames"]) == 4
    assert payload["evidence"]["comparison"] == {"before": 0, "after": 3}
    assert payload["evidence"]["boundingBoxes"] is True
    assert payload["timeline"][0]["action"] == "AI_DETECTED"

    secondary = (await client.get("/api/alerts/GJ-20260905-017")).json()
    assert set(secondary["relatedMission"]) >= {"id", "name", "status", "droneId", "route"}
    assert secondary["alert"]["coordinate"]
    assert secondary["alert"]["confidence"]

    failed = await client.get("/api/alerts/GJ-20260905-031/evidence", params={"simulateFailure": "true"})
    assert failed.status_code == 503
    assert failed.json()["detail"] == "EVIDENCE_TEMPORARILY_UNAVAILABLE"
    retried = await client.get("/api/alerts/GJ-20260905-031/evidence")
    assert retried.status_code == 200
    assert len(retried.json()["frames"]) == 4


@pytest.mark.asyncio
async def test_alert_confirm_and_false_positive_require_reason_and_append_audit(client: AsyncClient):
    confirmed = await client.post("/api/alerts/GJ-20260905-031/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["alert"]["status"] == "PROCESSING"
    assert confirmed.json()["timeline"][-1]["action"] == "ALERT_CONFIRMED"
    assert confirmed.json()["timeline"][-1]["actor"] == "王警官"
    assert confirmed.json()["timeline"][-1]["occurredAt"]

    no_reason = await client.post("/api/alerts/GJ-20260905-026/false-positive", json={"reasons": []})
    assert no_reason.status_code == 422
    blank_reason = await client.post("/api/alerts/GJ-20260905-026/false-positive", json={"reasons": ["  "]})
    assert blank_reason.status_code == 422
    unchanged = await client.get("/api/alerts/GJ-20260905-026")
    assert unchanged.json()["alert"]["status"] == "PROCESSING"

    false_positive = await client.post(
        "/api/alerts/GJ-20260905-026/false-positive",
        json={"reasons": ["光影干扰", "模型误识别"]},
    )
    assert false_positive.status_code == 200
    assert false_positive.json()["alert"]["status"] == "FALSE_POSITIVE"
    entry = false_positive.json()["timeline"][-1]
    assert entry["action"] == "ALERT_MARKED_FALSE_POSITIVE"
    assert entry["actor"] == "王警官"
    assert "光影干扰" in entry["detail"]


@pytest.mark.asyncio
async def test_alert_transfer_escalate_resolve_and_emergency_reminder_close_loop(client: AsyncClient):
    confirmed = await client.post("/api/alerts/GJ-20260905-031/confirm")
    assert confirmed.json()["alert"]["status"] == "PROCESSING"

    transferred = await client.post("/api/alerts/GJ-20260905-031/transfer", json={"target": "交管二大队"})
    assert transferred.status_code == 200
    assert transferred.json()["transferId"].startswith("ZP-MOCK-")

    unconfirmed = await client.post("/api/alerts/GJ-20260905-031/escalate", json={"confirmed": False})
    assert unconfirmed.status_code == 409
    escalated = await client.post("/api/alerts/GJ-20260905-031/escalate", json={"confirmed": True})
    assert escalated.json()["eventId"].startswith("SJ-MOCK-")

    missing_result = await client.post("/api/alerts/GJ-20260905-031/resolve", json={"result": "  "})
    assert missing_result.status_code == 422
    resolved = await client.post("/api/alerts/GJ-20260905-031/resolve", json={"result": "现场已恢复通行"})
    assert resolved.json()["alert"]["status"] == "RESOLVED"
    assert [item["action"] for item in resolved.json()["timeline"][-3:]] == [
        "ALERT_TRANSFERRED", "ALERT_ESCALATED", "ALERT_RESOLVED"
    ]

    await client.post("/api/demo/reset", json={"confirmed": True})
    not_yet = await client.post("/api/alerts/emergency-reminders", json={"elapsedMinutes": 5})
    assert not_yet.json()["alerts"] == []
    reminder = await client.post("/api/alerts/emergency-reminders", json={"elapsedMinutes": 6})
    assert reminder.status_code == 200
    assert reminder.json()["alerts"][0]["id"] == "GJ-20260905-031"
    assert reminder.json()["alerts"][0]["overdue"] is True
    repeated = await client.post("/api/alerts/emergency-reminders", json={"elapsedMinutes": 7})
    assert repeated.json()["alerts"][0]["id"] == "GJ-20260905-031"
    audit = (await client.get("/api/audit")).json()["audit"]
    assert len([item for item in audit if item["action"] == "ALERT_EMERGENCY_REMINDER"]) == 1


@pytest.mark.asyncio
async def test_report_export_generates_excel_pdf_and_audits(client: AsyncClient):
    payload = {
        "template": "周报", "format": "xlsx", "fields": ["台账编号", "巡查里程"],
        "filters": {"district": "中心老城区"},
        "stats": {"累计任务": "5项", "巡查里程": "62.9km"},
        "rows": [{"台账编号": "TZ-20260905-009", "巡查里程": "12.4"}],
        "analysisDimensions": {"dates": ["2026-09-01", "2026-09-05"], "districts": ["中心老城区"]},
        "operator": "王警官",
    }
    excel = await client.post("/api/reports/export", json=payload)
    assert excel.status_code == 200
    assert excel.content.startswith(b"PK")
    with ZipFile(BytesIO(excel.content)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert 'r="B7"' in sheet
    assert "巡查里程" in sheet
    assert "attachment" in excel.headers["content-disposition"]

    template_markers = {
        "日报": "当日执行", "周报": "七日趋势",
        "月报": "月度覆盖", "自定义": "自定义视图",
    }
    for template, marker in template_markers.items():
        payload["template"] = template
        rendered = await client.post("/api/reports/export", json=payload)
        with ZipFile(BytesIO(rendered.content)) as archive:
            template_sheet = archive.read("xl/worksheets/sheet1.xml").decode()
        assert marker in template_sheet
        if template == "周报":
            assert "覆盖 2 个有数据日期" in template_sheet

    payload["template"] = "周报"
    payload["format"] = "pdf"
    pdf = await client.post("/api/reports/export", json=payload)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert len(pdf.content) > 5000

    payload["simulateFailure"] = True
    failed = await client.post("/api/reports/export", json=payload)
    assert failed.status_code == 503
    audit = (await client.get("/api/audit")).json()["audit"]
    exports = [item for item in audit if item["action"] == "REPORT_EXPORTED"]
    assert [item["result"] for item in exports[-3:]] == ["SUCCESS", "SUCCESS", "FAILED"]
    assert all(item["actor"] == "王警官" for item in exports[-3:])
    assert "中心老城区" in exports[-1]["detail"]
