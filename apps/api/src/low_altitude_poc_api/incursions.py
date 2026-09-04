from __future__ import annotations

import json
import math
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import Float, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from low_altitude_poc_api.geometry import point_location, polygon_ring
from low_altitude_poc_api.spatial_rules import effective_rule_records


class IncursionBase(DeclarativeBase):
    pass


class IncursionState(IncursionBase):
    __tablename__ = "incursion_states"

    drone_id: Mapped[str] = mapped_column(String, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    candidate_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovery_started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    active_alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_source_time: Mapped[str] = mapped_column(String)


class IncursionAlertRecord(IncursionBase):
    __tablename__ = "incursion_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    drone_id: Mapped[str] = mapped_column(String, index=True)
    rule_id: Mapped[str] = mapped_column(String, index=True)
    rule_version: Mapped[int] = mapped_column(Integer)
    rule_type: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)
    longitude: Mapped[float] = mapped_column(Float)
    latitude: Mapped[float] = mapped_column(Float)
    altitude_m: Mapped[float] = mapped_column(Float)
    platform_received_time: Mapped[str] = mapped_column(String)
    source_type: Mapped[str] = mapped_column(String)
    rule_snapshot: Mapped[str] = mapped_column(String)


class IncursionAlert(BaseModel):
    id: int
    drone_id: str
    rule_id: str
    rule_version: int
    rule_type: Literal["no_fly_zone", "geofence"]
    reason: str
    started_at: datetime
    ended_at: datetime | None
    longitude: float
    latitude: float
    altitude_m: float
    platform_received_time: datetime
    source_type: Literal["simulated", "real"]
    rule_snapshot: dict[str, Any]


def _distance_to_boundary_m(
    point: tuple[float, float], ring: list[tuple[float, float]]
) -> float:
    longitude, latitude = point
    latitude_scale = 111_320.0
    longitude_scale = latitude_scale * math.cos(math.radians(latitude))
    px, py = longitude * longitude_scale, latitude * latitude_scale
    best = math.inf
    for start, end in pairwise(ring):
        ax, ay = start[0] * longitude_scale, start[1] * latitude_scale
        bx, by = end[0] * longitude_scale, end[1] * latitude_scale
        dx, dy = bx - ax, by - ay
        length_squared = dx * dx + dy * dy
        parameter = (
            max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared))
            if length_squared
            else 0.0
        )
        closest_x, closest_y = ax + parameter * dx, ay + parameter * dy
        best = min(best, math.hypot(px - closest_x, py - closest_y))
    return best


def evaluate_incursions(
    database: Session,
    *,
    drone_id: str,
    longitude: float,
    latitude: float,
    altitude_m: float,
    source_time: datetime,
    flight_state: str,
    platform_received_time: datetime,
    source_type: Literal["simulated", "real"],
    duration_seconds: float = 2.0,
    max_gap_seconds: float = 5.0,
    boundary_buffer_m: float = 5.0,
) -> None:
    if flight_state != "flying":
        for state in database.scalars(
            select(IncursionState).where(IncursionState.drone_id == drone_id)
        ):
            previous_source_time = datetime.fromisoformat(state.last_source_time)
            if source_time > previous_source_time:
                state.last_source_time = source_time.isoformat()
                state.candidate_started_at = None
                state.candidate_rule_version = None
                state.recovery_started_at = None
        return
    effective = effective_rule_records(database, source_time, source_time)
    evaluations: dict[str, tuple[bool | None, Any, Any]] = {}
    for record, rule in effective:
        ring = polygon_ring(rule.geometry)
        location = point_location((longitude, latitude), ring)
        beyond_buffer = (
            _distance_to_boundary_m((longitude, latitude), ring) >= boundary_buffer_m
        )
        within_altitude = rule.min_altitude_m <= altitude_m <= rule.max_altitude_m
        if rule.rule_type == "no_fly_zone":
            violating = (
                False
                if not within_altitude
                else None
                if not beyond_buffer
                else location == "inside"
            )
        else:
            violating = (
                None
                if not beyond_buffer and within_altitude
                else location == "outside" or not within_altitude
            )
        evaluations[rule.rule_id] = (violating, record, rule)

    states = {
        state.rule_id: state
        for state in database.scalars(
            select(IncursionState).where(IncursionState.drone_id == drone_id)
        )
    }
    for rule_id in set(states) | set(evaluations):
        state = states.get(rule_id)
        if state is not None:
            previous_source_time = datetime.fromisoformat(state.last_source_time)
            if source_time <= previous_source_time:
                continue
            if (source_time - previous_source_time).total_seconds() > max_gap_seconds:
                state.candidate_started_at = None
                state.candidate_rule_version = None
                state.recovery_started_at = None
        violating, record, rule = evaluations.get(rule_id, (False, None, None))
        if state is None:
            state = IncursionState(
                drone_id=drone_id,
                rule_id=rule_id,
                last_source_time=source_time.isoformat(),
            )
            database.add(state)
        state.last_source_time = source_time.isoformat()
        if record is not None:
            active_alert = (
                database.get(IncursionAlertRecord, state.active_alert_id)
                if state.active_alert_id is not None
                else None
            )
            version_changed = state.candidate_rule_version not in {
                None,
                record.version,
            } or (
                active_alert is not None and active_alert.rule_version != record.version
            )
            if version_changed:
                if active_alert is not None:
                    active_alert.ended_at = source_time.isoformat()
                    state.active_alert_id = None
                state.candidate_started_at = None
                state.candidate_rule_version = None
                state.recovery_started_at = None
        if violating is None:
            if state.active_alert_id is None:
                state.candidate_started_at = None
                state.candidate_rule_version = None
            state.recovery_started_at = None
            continue
        if violating:
            state.recovery_started_at = None
            if state.active_alert_id is None and (
                state.candidate_rule_version != record.version
                or state.candidate_started_at is None
            ):
                state.candidate_rule_version = record.version
                state.candidate_started_at = source_time.isoformat()
            candidate_started_at = datetime.fromisoformat(state.candidate_started_at)
            if (
                state.active_alert_id is None
                and (source_time - candidate_started_at).total_seconds()
                >= duration_seconds
            ):
                reason = (
                    "无人机持续进入禁飞区"
                    if rule.rule_type == "no_fly_zone"
                    else "无人机持续超出电子围栏"
                )
                alert = IncursionAlertRecord(
                    drone_id=drone_id,
                    rule_id=rule_id,
                    rule_version=record.version,
                    rule_type=rule.rule_type,
                    reason=reason,
                    started_at=candidate_started_at.isoformat(),
                    longitude=longitude,
                    latitude=latitude,
                    altitude_m=altitude_m,
                    platform_received_time=platform_received_time.isoformat(),
                    source_type=source_type,
                    rule_snapshot=json.dumps(
                        {
                            **json.loads(record.snapshot),
                            "version": record.version,
                            "actor": record.actor,
                            "published_at": record.published_at,
                        }
                    ),
                )
                database.add(alert)
                database.flush()
                state.active_alert_id = alert.id
        else:
            state.candidate_started_at = None
            state.candidate_rule_version = None
            if state.active_alert_id is not None:
                if state.recovery_started_at is None:
                    state.recovery_started_at = source_time.isoformat()
                recovery_started_at = datetime.fromisoformat(state.recovery_started_at)
                if (
                    source_time - recovery_started_at
                ).total_seconds() >= duration_seconds:
                    alert = database.get(IncursionAlertRecord, state.active_alert_id)
                    if alert is not None:
                        alert.ended_at = source_time.isoformat()
                    state.active_alert_id = None
                    state.recovery_started_at = None


def list_incursion_alerts(database: Session) -> list[IncursionAlert]:
    records = database.scalars(
        select(IncursionAlertRecord).order_by(IncursionAlertRecord.id.desc())
    ).all()
    return [
        IncursionAlert.model_validate(
            {
                **{
                    field: getattr(record, field)
                    for field in IncursionAlert.model_fields
                    if field != "rule_snapshot"
                },
                "rule_snapshot": json.loads(record.rule_snapshot),
            }
        )
        for record in records
    ]
