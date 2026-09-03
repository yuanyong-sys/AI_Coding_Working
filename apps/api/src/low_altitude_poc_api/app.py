import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI, status
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator
from sqlalchemy import Float, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


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
    track: list["TrackPoint"]


class TrackPoint(BaseModel):
    event_id: str
    longitude: float
    latitude: float
    altitude_m: float
    source_time: datetime


class SituationSnapshot(BaseModel):
    drones: list[DroneSnapshot]


def create_app(database_url: str | None = None) -> FastAPI:
    database_url = database_url or os.getenv(
        "LOW_ALTITUDE_DATABASE_URL", "sqlite:///./low_altitude_poc.db"
    )
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    app = FastAPI(title="无人机低空智慧调度平台 POC")

    @app.post(
        "/api/telemetry/batches",
        response_model=IngestResult,
        response_model_exclude_defaults=True,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def ingest_telemetry(batch: TelemetryBatch) -> IngestResult:
        received_at = datetime.now(UTC)
        accepted = 0
        errors: list[IngestError] = []
        with Session(engine) as session:
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
                values = event.model_dump(mode="json", exclude={"event_id"})
                values["platform_received_time"] = received_at.isoformat().replace(
                    "+00:00", "Z"
                )
                session.add(TelemetryPoint(event_id=event.event_id, **values))
                state = session.get(LatestDroneState, event.drone_id)
                if state is None or event.source_time > datetime.fromisoformat(
                    state.source_time
                ):
                    if state is None:
                        session.add(LatestDroneState(**values))
                        continue
                    for field, value in values.items():
                        setattr(state, field, value)
            session.commit()
        return IngestResult(accepted=accepted, rejected=len(errors), errors=errors)

    @app.get("/api/situation/snapshot", response_model=SituationSnapshot)
    def get_situation_snapshot() -> SituationSnapshot:
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
            return SituationSnapshot(
                drones=[
                    DroneSnapshot.model_validate(
                        {
                            **{
                                field: getattr(state, field)
                                for field in DroneSnapshot.model_fields
                                if field != "track"
                            },
                            "track": track_by_drone.get(state.drone_id, []),
                        }
                    )
                    for state in states
                ]
            )

    return app
