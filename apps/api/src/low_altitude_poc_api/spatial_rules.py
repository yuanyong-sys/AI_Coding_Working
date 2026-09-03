from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Engine, Integer, String, UniqueConstraint, func, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from low_altitude_poc_api.auth import AuthenticatedUser, AuthService
from low_altitude_poc_api.geometry import polygon_ring


class SpatialBase(DeclarativeBase):
    pass


class SpatialRuleDraftRecord(SpatialBase):
    __tablename__ = "spatial_rule_drafts"

    rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    snapshot: Mapped[str] = mapped_column(String)


class SpatialRuleVersionRecord(SpatialBase):
    __tablename__ = "spatial_rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String)
    published_at: Mapped[str] = mapped_column(String)


class SpatialRuleDraft(BaseModel):
    rule_id: str
    name: str
    rule_type: Literal["no_fly_zone", "geofence"]
    geometry: dict[str, object]
    min_altitude_m: float
    max_altitude_m: float
    valid_from: datetime
    valid_to: datetime
    source: str
    coordinate_reference: Literal["WGS84"] = "WGS84"


class SpatialRuleVersion(SpatialRuleDraft):
    version: int
    actor: str
    published_at: datetime


class SpatialRuleList(BaseModel):
    rules: list[SpatialRuleVersion]


class SpatialRuleHistory(BaseModel):
    versions: list[SpatialRuleVersion]


def effective_rule_records(
    database: Session, starts_at: datetime, ends_at: datetime
) -> list[tuple[SpatialRuleVersionRecord, SpatialRuleDraft]]:
    records = database.scalars(
        select(SpatialRuleVersionRecord).order_by(
            SpatialRuleVersionRecord.rule_id,
            SpatialRuleVersionRecord.version.desc(),
        )
    ).all()
    records_by_rule: dict[str, list[SpatialRuleVersionRecord]] = defaultdict(list)
    for record in records:
        records_by_rule[record.rule_id].append(record)

    effective: list[tuple[SpatialRuleVersionRecord, SpatialRuleDraft]] = []
    for rule_records in records_by_rule.values():
        higher_version_intervals: list[tuple[datetime, datetime]] = []
        for record in rule_records:
            rule = SpatialRuleDraft.model_validate_json(record.snapshot)
            intersection_start = max(rule.valid_from, starts_at)
            intersection_end = min(rule.valid_to, ends_at)
            if intersection_start > intersection_end:
                continue
            remaining = [(intersection_start, intersection_end)]
            for covered_start, covered_end in higher_version_intervals:
                next_remaining: list[tuple[datetime, datetime]] = []
                for remaining_start, remaining_end in remaining:
                    if covered_end < remaining_start or covered_start > remaining_end:
                        next_remaining.append((remaining_start, remaining_end))
                        continue
                    if remaining_start < covered_start:
                        next_remaining.append(
                            (
                                remaining_start,
                                covered_start - timedelta(microseconds=1),
                            )
                        )
                    if remaining_end > covered_end:
                        next_remaining.append(
                            (
                                covered_end + timedelta(microseconds=1),
                                remaining_end,
                            )
                        )
                remaining = next_remaining
            effective.extend(
                (
                    record,
                    rule.model_copy(
                        update={"valid_from": interval_start, "valid_to": interval_end}
                    ),
                )
                for interval_start, interval_end in remaining
            )
            higher_version_intervals.append((intersection_start, intersection_end))
    return effective


def configure_spatial_rules(app: FastAPI, engine: Engine, auth: AuthService) -> None:
    SpatialBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE VIRTUAL TABLE IF NOT EXISTS spatial_rule_rtree "
                "USING rtree(id, min_longitude, max_longitude, min_latitude, max_latitude)"
            )
        )

    def require_spatial_admin(
        user: AuthenticatedUser = Depends(auth.require_user),  # noqa: B008
    ) -> AuthenticatedUser:
        if "spatial:manage" not in user.capabilities:
            auth.record_audit(user.username, "spatial_rule_write_denied", "denied")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    def save_draft(draft: SpatialRuleDraft, user: AuthenticatedUser) -> None:
        with Session(engine) as database:
            record = database.get(SpatialRuleDraftRecord, draft.rule_id)
            snapshot = draft.model_dump_json()
            if record is None:
                database.add(
                    SpatialRuleDraftRecord(rule_id=draft.rule_id, snapshot=snapshot)
                )
            else:
                record.snapshot = snapshot
            auth.record_audit(
                user.username,
                "spatial_rule_draft_saved",
                "allowed",
                subject=f"spatial_rule:{draft.rule_id}",
                database=database,
            )
            database.commit()

    @app.post(
        "/api/spatial-rules/drafts",
        response_model=SpatialRuleDraft,
        status_code=status.HTTP_201_CREATED,
    )
    def create_draft(
        draft: SpatialRuleDraft,
        user: AuthenticatedUser = Depends(require_spatial_admin),  # noqa: B008
    ) -> SpatialRuleDraft:
        save_draft(draft, user)
        return draft

    @app.put(
        "/api/spatial-rules/drafts/{rule_id}",
        response_model=SpatialRuleDraft,
    )
    def update_draft(
        rule_id: str,
        draft: SpatialRuleDraft,
        user: AuthenticatedUser = Depends(require_spatial_admin),  # noqa: B008
    ) -> SpatialRuleDraft:
        if draft.rule_id != rule_id:
            auth.record_audit(
                user.username,
                "spatial_rule_draft_save_failed",
                "denied",
                subject=f"spatial_rule:{rule_id}",
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT)
        save_draft(draft, user)
        return draft

    @app.post(
        "/api/spatial-rules/drafts/{rule_id}/publish",
        response_model=SpatialRuleVersion,
        status_code=status.HTTP_201_CREATED,
    )
    def publish_draft(
        rule_id: str,
        user: AuthenticatedUser = Depends(require_spatial_admin),  # noqa: B008
    ) -> SpatialRuleVersion:
        with Session(engine) as database:
            database.execute(text("BEGIN IMMEDIATE"))
            draft_record = database.get(SpatialRuleDraftRecord, rule_id)
            if draft_record is None:
                auth.record_audit(
                    user.username,
                    "spatial_rule_publish_failed",
                    "denied",
                    subject=f"spatial_rule:{rule_id}",
                    database=database,
                )
                database.commit()
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            draft = SpatialRuleDraft.model_validate_json(draft_record.snapshot)
            try:
                ring = polygon_ring(draft.geometry)
                if draft.min_altitude_m > draft.max_altitude_m:
                    raise ValueError("最低高度不能高于最高高度")
                if (
                    draft.valid_from.tzinfo is None
                    or draft.valid_to.tzinfo is None
                    or draft.valid_from >= draft.valid_to
                ):
                    raise ValueError("有效开始时间必须早于结束时间")
            except ValueError as error:
                auth.record_audit(
                    user.username,
                    "spatial_rule_publish_failed",
                    "denied",
                    subject=f"spatial_rule:{rule_id}",
                    database=database,
                )
                database.commit()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=str(error),
                ) from error
            latest_version = database.scalar(
                select(func.max(SpatialRuleVersionRecord.version)).where(
                    SpatialRuleVersionRecord.rule_id == rule_id
                )
            )
            version = (latest_version or 0) + 1
            published_at = datetime.now(UTC)
            database.add(
                version_record := SpatialRuleVersionRecord(
                    rule_id=rule_id,
                    version=version,
                    snapshot=draft_record.snapshot,
                    actor=user.username,
                    published_at=published_at.isoformat(),
                )
            )
            database.flush()
            longitudes = [point[0] for point in ring]
            latitudes = [point[1] for point in ring]
            database.execute(
                text(
                    "INSERT INTO spatial_rule_rtree "
                    "(id, min_longitude, max_longitude, min_latitude, max_latitude) "
                    "VALUES (:id, :min_longitude, :max_longitude, :min_latitude, :max_latitude)"
                ),
                {
                    "id": version_record.id,
                    "min_longitude": min(longitudes),
                    "max_longitude": max(longitudes),
                    "min_latitude": min(latitudes),
                    "max_latitude": max(latitudes),
                },
            )
            auth.record_audit(
                user.username,
                "spatial_rule_published",
                "allowed",
                subject=f"spatial_rule:{rule_id}:v{version}",
                database=database,
            )
            database.commit()
        return SpatialRuleVersion(
            **draft.model_dump(),
            version=version,
            actor=user.username,
            published_at=published_at,
        )

    def version_response(record: SpatialRuleVersionRecord) -> SpatialRuleVersion:
        return SpatialRuleVersion(
            **json.loads(record.snapshot),
            version=record.version,
            actor=record.actor,
            published_at=datetime.fromisoformat(record.published_at),
        )

    @app.get("/api/spatial-rules/active", response_model=SpatialRuleList)
    def active_rules(
        _user: AuthenticatedUser = Depends(auth.require_user),  # noqa: B008
    ) -> SpatialRuleList:
        now = datetime.now(UTC)
        with Session(engine) as database:
            records = effective_rule_records(database, now, now)
            return SpatialRuleList(
                rules=[version_response(record) for record, _rule in records]
            )

    @app.get(
        "/api/spatial-rules/{rule_id}/versions",
        response_model=SpatialRuleHistory,
    )
    def rule_history(
        rule_id: str,
        _user: AuthenticatedUser = Depends(auth.require_user),  # noqa: B008
    ) -> SpatialRuleHistory:
        with Session(engine) as database:
            records = database.scalars(
                select(SpatialRuleVersionRecord)
                .where(SpatialRuleVersionRecord.rule_id == rule_id)
                .order_by(SpatialRuleVersionRecord.version)
            ).all()
            return SpatialRuleHistory(
                versions=[version_response(record) for record in records]
            )
