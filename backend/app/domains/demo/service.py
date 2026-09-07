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
        "alerts": [_row(item) for item in alerts],
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
    return {"id": f"AUDIT-{item.id:04d}", "action": item.action, "snapshotVersion": item.snapshot_version, "occurredAt": item.occurred_at.isoformat()}


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
        backup_drone=draft.backupDrone,
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
