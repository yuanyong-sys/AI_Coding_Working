import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.alert.models import Alert
from app.domains.audit.models import Audit, SystemMeta
from app.domains.drone.models import Drone
from app.domains.ledger.models import Ledger
from app.domains.mission.models import Mission
from app.domains.demo.snapshot import ALERTS, DRONES, LEDGERS, MISSIONS, SCHEMA_VERSION, SNAPSHOT_VERSION


async def ensure_seeded(session: AsyncSession, *, legacy_json: Path | None = None) -> None:
    schema_version = await session.get(SystemMeta, "schema_version")
    if schema_version and schema_version.value == str(SCHEMA_VERSION):
        return
    if legacy_json and legacy_json.exists():
        document = json.loads(legacy_json.read_text(encoding="utf-8"))
        await import_legacy_state(session, document)
        return
    await restore_business_state(session, record_audit=False)


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
            status=item["status"],
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
    return row


def _ledger_row(item: Ledger) -> dict:
    row = _row(item)
    row["mileageKm"] = row.pop("mileage_km")
    row["clueCount"] = row.pop("clue_count")
    row["taskId"] = row.pop("task_id")
    return row


def serialize_audit(item: Audit) -> dict:
    return {"id": f"AUDIT-{item.id:04d}", "action": item.action, "snapshotVersion": item.snapshot_version, "occurredAt": item.occurred_at.isoformat()}
