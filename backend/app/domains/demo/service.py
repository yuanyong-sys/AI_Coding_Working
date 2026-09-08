import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.alert.models import Alert
from app.domains.audit.models import Audit, SystemMeta
from app.domains.drone.models import Drone
from app.domains.ledger.models import Ledger
from app.domains.mission.models import Mission
from app.domains.demo.snapshot import ALERTS, DRONES, LEDGERS, MISSIONS, SCHEMA_VERSION, SNAPSHOT_VERSION
from app.domains.demo.schemas import MissionDraft

STATUS_ALIASES = {"PENDING": "PENDING_DISPATCH", "IN_PROGRESS": "RUNNING", "DISPATCHED": "PENDING_EXECUTION"}


async def ensure_seeded(session: AsyncSession, *, legacy_json: Path | None = None) -> None:
    schema_version = await session.get(SystemMeta, "schema_version")
    if schema_version and schema_version.value == str(SCHEMA_VERSION):
        await normalize_mission_statuses(session)
        return
    if legacy_json and legacy_json.exists():
        document = json.loads(legacy_json.read_text(encoding="utf-8"))
        await import_legacy_state(session, document)
        return
    await restore_business_state(session, record_audit=False)


async def normalize_mission_statuses(session: AsyncSession) -> None:
    missions = (await session.scalars(select(Mission))).all()
    changed = False
    for mission in missions:
        if mission.status in STATUS_ALIASES:
            mission.status = STATUS_ALIASES[mission.status]
            changed = True
    if changed:
        await session.commit()


async def import_legacy_state(session: AsyncSession, document: dict) -> None:
    session.add_all(
        Drone(
            id=item["id"],
            status=item["status"],
            battery=item["battery"],
            task_id=item.get("taskId"),
            task=item["task"],
            x=item["x"],
            y=item["y"],
            demo=item.get("demo", True),
        )
        for item in document.get("drones", [])
    )
    session.add_all(
        Mission(
            id=item["id"],
            name=item["name"],
            status=STATUS_ALIASES.get(item["status"], item["status"]),
            progress=item["progress"],
            overdue=item["overdue"],
            demo=item.get("demo", True),
        )
        for item in document.get("tasks", [])
    )
    session.add_all(
        Alert(
            id=item["id"],
            type=item["type"],
            level=item["level"],
            status=item["status"],
            location=item["location"],
            time=item["time"],
            x=item["x"],
            y=item["y"],
            demo=item.get("demo", True),
        )
        for item in document.get("alerts", [])
    )
    session.add_all(
        Ledger(
            id=item["id"],
            task_id=item["taskId"],
            mileage_km=item["mileageKm"],
            clue_count=item["clueCount"],
            demo=item.get("demo", True),
        )
        for item in document.get("ledgers", [])
    )
    for entry in document.get("audit", []):
        occurred_at = datetime.fromisoformat(entry["occurredAt"].replace("Z", "+00:00"))
        session.add(
            Audit(
                action=entry["action"],
                snapshot_version=entry["snapshotVersion"],
                occurred_at=occurred_at,
            )
        )
    await session.merge(SystemMeta(key="schema_version", value=str(SCHEMA_VERSION)))
    await session.merge(SystemMeta(key="legacy_json_imported", value="true"))
    await session.commit()


async def restore_business_state(session: AsyncSession, *, record_audit: bool = True) -> Audit | None:
    # AI-NOTE: Reset replaces only demo business tables; audit is intentionally append-only.
    for model in (Drone, Mission, Alert, Ledger):
        await session.execute(delete(model))
    session.add_all(Drone(id=i, status=s, battery=b, task_id=ti, task=t, x=x, y=y) for i, s, b, ti, t, x, y in DRONES)
    session.add_all(Mission(id=i, name=n, status=s, progress=p, overdue=o) for i, n, s, p, o in MISSIONS)
    session.add_all(Alert(id=i, type=t, level=l, status=s, location=loc, time=tm, x=x, y=y) for i, t, l, s, loc, tm, x, y in ALERTS)
    session.add_all(Ledger(id=i, task_id=t, mileage_km=m, clue_count=c) for i, t, m, c in LEDGERS)
    await session.merge(SystemMeta(key="schema_version", value=str(SCHEMA_VERSION)))
    audit = None
    if record_audit:
        audit = Audit(action="DEMO_RESET", snapshot_version=SNAPSHOT_VERSION, occurred_at=datetime.now(timezone.utc))
        session.add(audit)
    await session.commit()
    if audit:
        await session.refresh(audit)
    return audit


def _row(model: object) -> dict:
    values = {column.name: getattr(model, column.name) for column in model.__table__.columns}
    values["demo"] = bool(values.get("demo", True))
    return values


async def read_state(session: AsyncSession) -> dict:
    drones = (await session.scalars(select(Drone).order_by(Drone.id))).all()
    tasks = (await session.scalars(select(Mission))).all()
    alerts = (await session.scalars(select(Alert).order_by(Alert.time.desc()))).all()
    ledgers = (await session.scalars(select(Ledger))).all()
    audit = (await session.scalars(select(Audit).order_by(Audit.id))).all()
    return {
        "snapshotVersion": SNAPSHOT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "simulationClock": "2026-09-05T14:32:00+08:00",
        "drones": [_row(item) for item in drones],
        "tasks": [serialize_mission(item) for item in tasks],
        "alerts": [serialize_alert(item) for item in alerts],
        "ledgers": [_ledger_row(item) for item in ledgers],
        "audit": [serialize_audit(item) for item in audit],
    }


def serialize_mission(item: Mission) -> dict:
    row = _row(item)
    row["overdue"] = bool(row["overdue"])
    row["droneId"] = row.pop("drone_id")
    row["start"] = row.pop("start_time")
    row["end"] = row.pop("end_time")
    row["aiItems"] = json.loads(row.pop("ai_items") or "[]")
    row["date"] = row.pop("mission_date")
    row["type"] = row.pop("mission_type")
    row["backupDrone"] = row.pop("backup_drone")
    return row


def _ledger_row(item: Ledger) -> dict:
    row = _row(item)
    row["mileageKm"] = row.pop("mileage_km")
    row["clueCount"] = row.pop("clue_count")
    row["taskId"] = row.pop("task_id")
    return row


def serialize_audit(item: Audit) -> dict:
    return {
        "id": f"AUDIT-{item.id:04d}", "action": item.action,
        "snapshotVersion": item.snapshot_version, "occurredAt": item.occurred_at.isoformat(),
        "subjectId": item.subject_id, "result": item.result, "detail": item.detail,
        "actor": item.actor, "beforeState": item.before_state, "afterState": item.after_state,
        "failureReason": item.failure_reason,
    }


def serialize_alert(item: Alert) -> dict:
    row = _row(item)
    row["missionId"] = row.pop("mission_id")
    context = ALERT_CONTEXT.get(item.id, {})
    row["missionId"] = row["missionId"] or context.get("missionId")
    row["droneId"] = context.get("droneId")
    row["route"] = context.get("route")
    row["coordinate"] = context.get("coordinate")
    row["confidence"] = context.get("confidence")
    return row


ALERT_CONTEXT = {
    "GJ-20260905-031": {"missionId": "RW-20260905-012", "missionName": "福泉马场坪段夜间巡查", "droneId": "U-03", "route": "兰海高速都匀段", "coordinate": "107.5218°E · 26.2594°N", "confidence": 96},
    "GJ-20260905-026": {"missionId": "RW-20260905-009", "missionName": "荔波互通环线巡查", "droneId": "U-05", "route": "荔波互通环线", "coordinate": "107.8836°E · 25.4122°N", "confidence": 91},
    "GJ-20260905-024": {"missionId": "RW-20260905-007", "missionName": "贵定连接线隐患排查", "droneId": "U-07", "route": "贵定连接线", "coordinate": "107.2345°E · 26.5847°N", "confidence": 84},
    "GJ-20260905-019": {"missionId": "RW-20260905-006", "missionName": "厦蓉高速独山段巡查", "droneId": "U-02", "route": "厦蓉高速独山段", "coordinate": "107.5478°E · 25.8296°N", "confidence": 79},
    "GJ-20260905-017": {"missionId": "RW-20260905-009", "missionName": "福泉服务区入口巡查", "droneId": "U-05", "route": "兰海高速福泉段", "coordinate": "107.5336°E · 26.6720°N", "confidence": 78},
    "GJ-20260905-014": {"missionId": "RW-20260905-007", "missionName": "贵定连接线隐患排查", "droneId": "U-07", "route": "贵定连接线", "coordinate": "107.2345°E · 26.5847°N", "confidence": 82},
    "GJ-20260905-008": {"missionId": "RW-20260905-001", "missionName": "贵北高速惠水段航线巡检", "droneId": "U-01", "route": "贵北高速惠水段", "coordinate": "106.6547°E · 26.1318°N", "confidence": 75},
}


async def filter_alerts(
    session: AsyncSession, *, level: str | None, time_window: str, keyword: str
) -> list[dict]:
    alerts = (await session.scalars(select(Alert).order_by(Alert.time.desc()))).all()
    cutoff = {"30m": 30, "1h": 60}.get(time_window)
    keyword = keyword.casefold().strip()
    result = []
    simulation_minutes = 14 * 60 + 32
    for alert in alerts:
        hour, minute = (int(value) for value in alert.time.split(":")[:2])
        minutes = max(0, simulation_minutes - (hour * 60 + minute))
        if level and level != "all" and alert.level != level:
            continue
        if cutoff is not None and minutes > cutoff:
            continue
        if keyword and keyword not in f"{alert.id}{alert.type}{alert.location}".casefold():
            continue
        result.append(serialize_alert(alert))
    return result


def alert_evidence(alert_id: str) -> dict:
    return {
        "mode": "SIMULATED",
        "alertId": alert_id,
        "frames": [
            {"index": index, "time": time, "label": f"模拟证据帧 {index + 1}"}
            for index, time in enumerate(("14:02:31", "14:02:34", "14:02:37", "14:02:40"))
        ],
        "playbackSpeeds": [0.5, 1, 2],
        "comparison": {"before": 0, "after": 3},
        "boundingBoxes": True,
    }


async def alert_detail(session: AsyncSession, alert: Alert) -> dict:
    context = ALERT_CONTEXT.get(alert.id, {})
    mission_id = alert.mission_id or context.get("missionId")
    mission = await session.get(Mission, mission_id) if mission_id else None
    audits = (await session.scalars(
        select(Audit).where(Audit.subject_id == alert.id).order_by(Audit.id)
    )).all()
    detected = {
        "action": "AI_DETECTED", "actor": "AI 识别引擎",
        "occurredAt": f"2026-09-05T{alert.time}:00+08:00", "detail": f"识别到{alert.type}",
    }
    related_mission = serialize_mission(mission) if mission else {
        "id": mission_id, "name": context.get("missionName"), "status": "DEMO_REFERENCE",
    }
    related_mission["droneId"] = related_mission.get("droneId") or context.get("droneId")
    related_mission["route"] = related_mission.get("route") or context.get("route")
    return {
        "alert": serialize_alert(alert),
        "relatedMission": related_mission,
        "evidence": alert_evidence(alert.id),
        "timeline": [detected, *(serialize_audit(item) for item in audits)],
    }


async def record_alert_audit(
    session: AsyncSession, alert: Alert, *, action: str, detail: str,
    before_state: str, after_state: str,
) -> None:
    session.add(Audit(
        action=action, snapshot_version=SNAPSHOT_VERSION, occurred_at=datetime.now(timezone.utc),
        subject_id=alert.id, result="SUCCESS", detail=detail, actor="王警官",
        before_state=before_state, after_state=after_state,
    ))
    await session.commit()


async def get_actionable_alert(session: AsyncSession, alert_id: str) -> Alert:
    from fastapi import HTTPException
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if alert.status in {"RESOLVED", "FALSE_POSITIVE"}:
        raise HTTPException(status_code=409, detail="ALERT_ALREADY_CLOSED")
    return alert


async def trigger_emergency_reminders(session: AsyncSession, elapsed_minutes: int) -> list[dict]:
    alerts = (await session.scalars(select(Alert).where(
        Alert.level == "EMERGENCY", Alert.status.in_(("PENDING_VERIFICATION", "PENDING"))
    ))).all()
    simulated_now = 14 * 60 + 32 + elapsed_minutes
    overdue_alerts = []
    for alert in alerts:
        hour, minute = (int(value) for value in alert.time.split(":")[:2])
        if simulated_now - (hour * 60 + minute) > 5:
            overdue_alerts.append(alert)
    existing = set((await session.scalars(select(Audit.subject_id).where(
        Audit.action == "ALERT_EMERGENCY_REMINDER"
    ))).all())
    for alert in overdue_alerts:
        if alert.id not in existing:
            session.add(Audit(
                action="ALERT_EMERGENCY_REMINDER", snapshot_version=SNAPSHOT_VERSION,
                occurred_at=datetime.now(timezone.utc), subject_id=alert.id, result="SUCCESS",
                detail="紧急告警超过五个模拟分钟未核实，已置顶并站内提醒", actor="系统",
                before_state=alert.status, after_state=alert.status,
            ))
    await session.commit()
    return [{**serialize_alert(alert), "overdue": True} for alert in overdue_alerts]


DRONE_ALIASES = {f"警航-{index:02d}": f"U-{index:02d}" for index in range(1, 9)}
RESTRICTED_ROUTES = {"演示禁飞区航线"}
PROTOTYPE_PENDING = {
    "RW-20260905-018": "城北物流园夜间巡查",
    "RW-20260905-020": "中山路施工路段专项巡查",
    "RW-20260905-023": "夜市商圈秩序巡查",
}
PROTOTYPE_STATES = {
    **{key: "PENDING_DISPATCH" for key in PROTOTYPE_PENDING},
    **{key: "PENDING_EXECUTION" for key in ("RW-20260905-019", "RW-20260905-016", "RW-20260905-017", "RW-20260905-027", "RW-20260905-028")},
    **{key: "RUNNING" for key in ("RW-20260905-012", "RW-20260905-015", "RW-20260905-013", "RW-20260905-014", "RW-20260905-022", "RW-20260905-024", "RW-20260905-025", "RW-20260905-026")},
}


async def validate_mission(session: AsyncSession, draft: MissionDraft) -> dict:
    blockers = []
    canonical_drone_id = DRONE_ALIASES.get(draft.droneId, draft.droneId)
    drone = await session.get(Drone, canonical_drone_id)
    if draft.start >= draft.end:
        blockers.append({"code": "TIME_INVALID", "message": "结束时间必须晚于开始时间"})
    if drone is None:
        blockers.append({"code": "DRONE_NOT_FOUND", "message": "执行无人机不存在"})
    elif drone.battery < 30:
        blockers.append({"code": "LOW_BATTERY", "message": "无人机电量低于 30%"})
    elif drone.status == "OFFLINE":
        blockers.append({"code": "DRONE_UNAVAILABLE", "message": "执行无人机当前离线"})
    if draft.backupDrone not in {None, "", "不指定"}:
        backup_id = DRONE_ALIASES.get(draft.backupDrone, draft.backupDrone)
        backup = await session.get(Drone, backup_id)
        if backup is None or backup.status == "OFFLINE" or backup.battery < 30 or backup_id == canonical_drone_id:
            blockers.append({"code": "BACKUP_UNAVAILABLE", "message": "备用无人机不可用"})
    if draft.route in RESTRICTED_ROUTES:
        blockers.append({"code": "AIRSPACE_CONFLICT", "message": "航线命中演示空域限制"})
    if not draft.aiItems:
        blockers.append({"code": "AI_REQUIRED", "message": "至少选择一个 AI 识别项"})
    if draft.start < draft.end:
        start = draft.start.isoformat(timespec="minutes")
        end = draft.end.isoformat(timespec="minutes")
        query = select(Mission).where(
            Mission.drone_id == canonical_drone_id,
            Mission.mission_date == draft.date.isoformat(),
        )
        missions = (await session.scalars(query)).all()
        if any(item.start_time and item.end_time and start < item.end_time and item.start_time < end for item in missions):
            blockers.append({"code": "SCHEDULE_CONFLICT", "message": "所选无人机在该时间窗已有任务"})
    return {"canDispatch": not blockers, "blockers": blockers}


async def create_mission(session: AsyncSession, draft: MissionDraft) -> Mission:
    canonical_drone_id = DRONE_ALIASES.get(draft.droneId, draft.droneId)
    backup_drone = None if draft.backupDrone in {None, "", "不指定"} else DRONE_ALIASES.get(draft.backupDrone, draft.backupDrone)
    mission = Mission(
        id=f"RW-{uuid4().hex[:10].upper()}",
        name=draft.name,
        status="PENDING_DISPATCH",
        progress=0,
        overdue=False,
        start_time=draft.start.isoformat(timespec="minutes"),
        end_time=draft.end.isoformat(timespec="minutes"),
        drone_id=canonical_drone_id,
        route=draft.route,
        ai_items=json.dumps(draft.aiItems, ensure_ascii=False),
        mission_date=draft.date.isoformat(),
        mission_type=draft.type,
        priority=draft.priority,
        owner=draft.owner,
        area=draft.area,
        dock=draft.dock,
        backup_drone=backup_drone,
    )
    session.add(mission)
    await session.commit()
    await session.refresh(mission)
    return mission


async def batch_dispatch(session: AsyncSession, mission_ids: list[str]) -> dict:
    missions = (await session.scalars(select(Mission).where(Mission.id.in_(mission_ids)))).all()
    known_ids = {item.id for item in missions}
    for mission_id in mission_ids:
        if mission_id in PROTOTYPE_PENDING and mission_id not in known_ids:
            mission = Mission(id=mission_id, name=PROTOTYPE_PENDING[mission_id], status="PENDING_DISPATCH", progress=0, overdue=False)
            session.add(mission)
            missions.append(mission)
    eligible = [item for item in missions if item.status in {"PENDING", "PENDING_DISPATCH"}]
    for mission in eligible:
        mission.status = "PENDING_EXECUTION"
        session.add(Audit(
            action="MISSION_DISPATCH", snapshot_version=SNAPSHOT_VERSION,
            occurred_at=datetime.now(timezone.utc), subject_id=mission.id,
            result="SUCCESS", detail="批量模拟下发", actor="王警官",
            before_state="PENDING_DISPATCH", after_state="PENDING_EXECUTION",
        ))
    await session.commit()
    eligible_ids = {item.id for item in eligible}
    failed_ids = [mission_id for mission_id in mission_ids if mission_id not in eligible_ids]
    return {"successCount": len(eligible), "failedCount": len(failed_ids), "failedIds": failed_ids}


def can_transition(current: str, target: str) -> bool:
    allowed = {
        "PENDING": {"PENDING_EXECUTION", "TERMINATED"},
        "PENDING_DISPATCH": {"PENDING_EXECUTION", "TERMINATED"},
        "PENDING_EXECUTION": {"RUNNING", "TERMINATED"},
        "RUNNING": {"COMPLETED", "ABNORMAL", "TERMINATED"},
    }
    return target in allowed.get(current, set())


async def get_dispatchable_mission(session: AsyncSession, mission_id: str) -> Mission | None:
    mission = await session.get(Mission, mission_id)
    if mission is None and mission_id in PROTOTYPE_PENDING:
        mission = Mission(id=mission_id, name=PROTOTYPE_PENDING[mission_id], status="PENDING_DISPATCH", progress=0, overdue=False)
        session.add(mission)
    return mission


async def get_transitionable_mission(session: AsyncSession, mission_id: str) -> Mission | None:
    mission = await session.get(Mission, mission_id)
    if mission is None and mission_id in PROTOTYPE_STATES:
        mission = Mission(id=mission_id, name=PROTOTYPE_PENDING.get(mission_id, mission_id), status=PROTOTYPE_STATES[mission_id], progress=0, overdue=False)
        session.add(mission)
    return mission


def monitor_snapshot(mission: Mission) -> dict:
    seed = sum(ord(char) for char in mission.id)
    return {
        "missionId": mission.id,
        "status": mission.status,
        "video": {"mode": "SIMULATED", "label": "视频流占位"},
        "telemetry": {
            "altitudeM": 100 + seed % 40,
            "speedMps": round(8 + seed % 50 / 10, 1),
            "batteryPct": 90 - seed % 28,
            "signalDbm": -60 - seed % 15,
        },
        "trajectory": [[20, 130], [72, 106], [136, 79], [204, 50]],
        "controlState": mission.control_state,
        "simulated": True,
    }


async def record_audit(
    session: AsyncSession, *, action: str, mission_id: str, result: str, detail: str,
    before_state: str | None = None, after_state: str | None = None,
    failure_reason: str | None = None, actor: str = "王警官",
) -> Audit:
    entry = Audit(
        action=action,
        snapshot_version=SNAPSHOT_VERSION,
        occurred_at=datetime.now(timezone.utc),
        subject_id=mission_id,
        result=result,
        detail=detail,
        actor=actor,
        before_state=before_state,
        after_state=after_state,
        failure_reason=failure_reason,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


async def handle_mission_anomaly(session: AsyncSession, mission: Mission, kind: str) -> dict:
    before_state = mission.status
    existing = await session.scalar(select(Alert).where(Alert.mission_id == mission.id))
    if existing is None:
        existing = Alert(
            id=f"GJ-AUTO-{mission.id}",
            type="图传断链" if kind == "LINK_LOSS" else "低电量",
            level="IMPORTANT",
            status="PENDING_VERIFICATION",
            location=mission.route or mission.name,
            time=datetime.now().strftime("%H:%M"),
            x=470,
            y=420,
            mission_id=mission.id,
        )
        session.add(existing)
    backup = await session.get(Drone, mission.backup_drone) if mission.backup_drone else None
    if backup and backup.status != "OFFLINE" and backup.battery >= 30 and mission.drone_id != backup.id:
        mission.drone_id = DRONE_ALIASES.get(mission.backup_drone, mission.backup_drone)
        mission.control_state = "AUTO_BACKUP_SWITCH"
        outcome = "BACKUP_SWITCHED"
        action = "MISSION_AUTO_BACKUP_SWITCH"
    else:
        mission.status = "ABNORMAL"
        outcome = "ABNORMAL"
        action = "MISSION_MARKED_ABNORMAL"
    await record_audit(
        session, action=action, mission_id=mission.id, result="SUCCESS", detail=kind,
        before_state=before_state, after_state=mission.status,
    )
    await session.refresh(existing)
    return {"outcome": outcome, "mission": serialize_mission(mission), "alert": serialize_alert(existing)}
