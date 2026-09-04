import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator
from sqlalchemy import (
    Float,
    Integer,
    String,
    create_engine,
    delete,
    distinct,
    func,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from low_altitude_poc_api.auth import AuthenticatedUser, configure_auth
from low_altitude_poc_api.incursions import (
    IncursionAlert,
    IncursionBase,
    evaluate_incursions,
    list_incursion_alerts,
)
from low_altitude_poc_api.preflight import configure_preflight_validation
from low_altitude_poc_api.spatial_rules import configure_spatial_rules


class Base(DeclarativeBase):
    pass


class LatestDroneState(Base):
    __tablename__ = "latest_drone_states"

    drone_id: Mapped[str] = mapped_column(String, primary_key=True)
    longitude: Mapped[float] = mapped_column(Float)
    latitude: Mapped[float] = mapped_column(Float)
    altitude_m: Mapped[float] = mapped_column(Float)
    heading_deg: Mapped[float] = mapped_column(Float)
    speed_mps: Mapped[float] = mapped_column(Float)
    flight_state: Mapped[str] = mapped_column(String)
    source_time: Mapped[str] = mapped_column(String)
    platform_received_time: Mapped[str] = mapped_column(String)
    source_type: Mapped[str] = mapped_column(String)


class TelemetryPoint(Base):
    __tablename__ = "telemetry_points"

    source_type: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    drone_id: Mapped[str] = mapped_column(String, index=True)
    longitude: Mapped[float] = mapped_column(Float)
    latitude: Mapped[float] = mapped_column(Float)
    altitude_m: Mapped[float] = mapped_column(Float)
    heading_deg: Mapped[float] = mapped_column(Float)
    speed_mps: Mapped[float] = mapped_column(Float)
    flight_state: Mapped[str] = mapped_column(String)
    source_time: Mapped[str] = mapped_column(String, index=True)
    platform_received_time: Mapped[str] = mapped_column(String)


class TelemetrySortie(Base):
    __tablename__ = "telemetry_sorties"

    source_type: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    sortie_id: Mapped[str] = mapped_column(String, index=True)


class SituationEvent(Base):
    __tablename__ = "situation_events"

    cursor: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String)
    event_id: Mapped[str] = mapped_column(String)
    payload: Mapped[str] = mapped_column(String)


class TelemetryEvent(BaseModel):
    event_id: str
    drone_id: str
    longitude: float
    latitude: float
    altitude_m: float
    heading_deg: float
    speed_mps: float
    flight_state: str
    source_time: datetime
    source_type: Literal["simulated", "real"]
    sortie_id: str

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("must be between -180 and 180")
        return value

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("must be between -90 and 90")
        return value

    @field_validator("source_time")
    @classmethod
    def validate_source_time(cls, value: datetime, info: ValidationInfo) -> datetime:
        received_at: datetime = info.context["received_at"]
        if value.tzinfo is None:
            raise ValueError("must include a timezone")
        if value > received_at.replace(microsecond=0) + timedelta(minutes=5):
            raise ValueError("must not be more than 5 minutes in the future")
        return value


class TelemetryBatch(BaseModel):
    events: list[dict[str, Any]]


class IngestError(BaseModel):
    event_id: str
    field: str
    reason: str


class IngestResult(BaseModel):
    accepted: int
    rejected: int
    errors: list[IngestError] = Field(default_factory=list)


class DroneSnapshot(BaseModel):
    drone_id: str
    longitude: float
    latitude: float
    altitude_m: float
    heading_deg: float
    speed_mps: float
    flight_state: str
    source_time: datetime
    platform_received_time: datetime
    source_type: str
    data_status: Literal["current", "delayed", "offline"]
    track: list["TrackPoint"]


class TrackPoint(BaseModel):
    event_id: str
    longitude: float
    latitude: float
    altitude_m: float
    source_time: datetime


class SourceComposition(BaseModel):
    real: int
    simulated: int


class SituationMetrics(BaseModel):
    flight_sorties: int
    online_rate: float
    in_flight_count: int
    telemetry_delay_seconds: float
    source_composition: SourceComposition
    observation_window_seconds: int


class SituationSnapshot(BaseModel):
    drones: list[DroneSnapshot]
    incursion_alerts: list[IncursionAlert]
    metrics: SituationMetrics
    cursor: int


def create_app(
    database_url: str | None = None,
    demo_password: str | None = None,
    clock: Callable[[], datetime] | None = None,
    event_retention: int = 1000,
    incursion_duration_seconds: float | None = None,
    incursion_max_gap_seconds: float | None = None,
    incursion_boundary_buffer_m: float | None = None,
) -> FastAPI:
    clock = clock or (lambda: datetime.now(UTC))
    database_url = database_url or os.getenv(
        "LOW_ALTITUDE_DATABASE_URL", "sqlite:///./low_altitude_poc.db"
    )
    if incursion_duration_seconds is None:
        incursion_duration_seconds = float(
            os.getenv("LOW_ALTITUDE_INCURSION_DURATION_SECONDS", "2")
        )
    if incursion_boundary_buffer_m is None:
        incursion_boundary_buffer_m = float(
            os.getenv("LOW_ALTITUDE_INCURSION_BOUNDARY_BUFFER_M", "5")
        )
    if incursion_max_gap_seconds is None:
        incursion_max_gap_seconds = float(
            os.getenv("LOW_ALTITUDE_INCURSION_MAX_GAP_SECONDS", "5")
        )
    if (
        incursion_duration_seconds <= 0
        or incursion_max_gap_seconds <= 0
        or incursion_boundary_buffer_m < 0
    ):
        raise ValueError(
            "越界告警持续时间和最大遥测间隔必须大于零，边界缓冲区不得为负数"
        )
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    IncursionBase.metadata.create_all(engine)

    app = FastAPI(title="无人机低空智慧调度平台 POC")
    auth = configure_auth(app, engine, demo_password)
    require_user = auth.require_user
    configure_spatial_rules(app, engine, auth)
    configure_preflight_validation(app, engine, auth)

    @app.post(
        "/api/telemetry/batches",
        response_model=IngestResult,
        response_model_exclude_defaults=True,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def ingest_telemetry(batch: TelemetryBatch) -> IngestResult:
        received_at = clock()
        accepted = 0
        errors: list[IngestError] = []
        with Session(engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            for raw_event in batch.events:
                try:
                    event = TelemetryEvent.model_validate(
                        raw_event, context={"received_at": received_at}
                    )
                except ValidationError as validation_error:
                    issue = validation_error.errors()[0]
                    reason = str(issue.get("ctx", {}).get("error", issue["msg"]))
                    errors.append(
                        IngestError(
                            event_id=str(raw_event.get("event_id", "unknown")),
                            field=str(issue["loc"][-1]),
                            reason=reason,
                        )
                    )
                    continue
                accepted += 1
                existing_point = session.get(
                    TelemetryPoint, (event.source_type, event.event_id)
                )
                if existing_point is not None:
                    continue
                values = event.model_dump(
                    mode="json", exclude={"event_id", "sortie_id"}
                )
                values["platform_received_time"] = received_at.isoformat().replace(
                    "+00:00", "Z"
                )
                session.add(TelemetryPoint(event_id=event.event_id, **values))
                session.add(
                    TelemetrySortie(
                        source_type=event.source_type,
                        event_id=event.event_id,
                        sortie_id=event.sortie_id,
                    )
                )
                evaluate_incursions(
                    session,
                    drone_id=event.drone_id,
                    longitude=event.longitude,
                    latitude=event.latitude,
                    altitude_m=event.altitude_m,
                    source_time=event.source_time,
                    flight_state=event.flight_state,
                    platform_received_time=received_at,
                    source_type=event.source_type,
                    duration_seconds=incursion_duration_seconds,
                    max_gap_seconds=incursion_max_gap_seconds,
                    boundary_buffer_m=incursion_boundary_buffer_m,
                )
                session.add(
                    SituationEvent(
                        source_type=event.source_type,
                        event_id=event.event_id,
                        payload=event.model_dump_json(exclude_none=True),
                    )
                )
                state = session.get(LatestDroneState, event.drone_id)
                if state is None or event.source_time > datetime.fromisoformat(
                    state.source_time
                ):
                    if state is None:
                        session.add(LatestDroneState(**values))
                        continue
                    for field, value in values.items():
                        setattr(state, field, value)
            session.flush()
            latest_cursor = session.scalar(select(func.max(SituationEvent.cursor))) or 0
            session.execute(
                delete(SituationEvent).where(
                    SituationEvent.cursor <= latest_cursor - max(event_retention, 1)
                )
            )
            session.commit()
        return IngestResult(accepted=accepted, rejected=len(errors), errors=errors)

    @app.get("/api/situation/snapshot", response_model=SituationSnapshot)
    def get_situation_snapshot(
        _user: AuthenticatedUser = Depends(require_user),  # noqa: B008
    ) -> SituationSnapshot:
        with Session(engine) as session:
            states = session.scalars(
                select(LatestDroneState).order_by(LatestDroneState.drone_id)
            ).all()
            track_by_drone: dict[str, list[TrackPoint]] = {}
            for point in session.scalars(
                select(TelemetryPoint).order_by(
                    TelemetryPoint.drone_id,
                    TelemetryPoint.source_time,
                    TelemetryPoint.event_id,
                )
            ):
                track_by_drone.setdefault(point.drone_id, []).append(
                    TrackPoint.model_validate(point, from_attributes=True)
                )
            now = clock()
            drones: list[DroneSnapshot] = []
            for state in states:
                platform_received_time = datetime.fromisoformat(
                    state.platform_received_time
                )
                delay_seconds = max(0.0, (now - platform_received_time).total_seconds())
                data_status: Literal["current", "delayed", "offline"] = "current"
                if delay_seconds > 30:
                    data_status = "offline"
                elif delay_seconds > 10:
                    data_status = "delayed"
                drones.append(
                    DroneSnapshot.model_validate(
                        {
                            **{
                                field: getattr(state, field)
                                for field in DroneSnapshot.model_fields
                                if field not in {"track", "data_status"}
                            },
                            "data_status": data_status,
                            "track": track_by_drone.get(state.drone_id, []),
                        }
                    )
                )
            online_drones = [
                drone for drone in drones if drone.data_status != "offline"
            ]
            return SituationSnapshot(
                drones=drones,
                incursion_alerts=list_incursion_alerts(session),
                metrics=SituationMetrics(
                    flight_sorties=session.scalar(
                        select(func.count(distinct(TelemetrySortie.sortie_id)))
                    )
                    or 0,
                    online_rate=(
                        round(len(online_drones) / len(drones) * 100, 1)
                        if drones
                        else 0.0
                    ),
                    in_flight_count=sum(
                        drone.flight_state == "flying" for drone in online_drones
                    ),
                    telemetry_delay_seconds=(
                        round(
                            max(
                                (now - drone.platform_received_time).total_seconds()
                                for drone in drones
                            ),
                            3,
                        )
                        if drones
                        else 0.0
                    ),
                    source_composition=SourceComposition(
                        real=sum(drone.source_type == "real" for drone in drones),
                        simulated=sum(
                            drone.source_type == "simulated" for drone in drones
                        ),
                    ),
                    observation_window_seconds=30,
                ),
                cursor=session.scalar(select(func.max(SituationEvent.cursor))) or 0,
            )

    @app.websocket("/api/situation/events")
    async def situation_events(
        websocket: WebSocket,
        after: int = 0,
        _user: AuthenticatedUser = Depends(require_user),  # noqa: B008
    ) -> None:
        await websocket.accept()

        async def send_available_events(current_cursor: int) -> int:
            with Session(engine) as session:
                oldest_cursor = session.scalar(select(func.min(SituationEvent.cursor)))
                latest_cursor = (
                    session.scalar(select(func.max(SituationEvent.cursor))) or 0
                )
                if oldest_cursor is not None and current_cursor < oldest_cursor - 1:
                    message = {
                        "type": "snapshot_required",
                        "cursor": latest_cursor,
                    }
                    next_cursor = latest_cursor
                    messages = [message]
                else:
                    event_records = session.scalars(
                        select(SituationEvent)
                        .where(SituationEvent.cursor > current_cursor)
                        .order_by(SituationEvent.cursor)
                    ).all()
                    messages = [
                        {
                            "type": "telemetry",
                            "cursor": event.cursor,
                            "event": json.loads(event.payload),
                        }
                        for event in event_records
                    ]
                    next_cursor = (
                        event_records[-1].cursor if event_records else current_cursor
                    )
            for message in messages:
                await websocket.send_json(message)
            return next_cursor

        try:
            after = await send_available_events(after)
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=0.1)
                except TimeoutError:
                    pass
                after = await send_available_events(after)
        except WebSocketDisconnect:
            return

    return app
