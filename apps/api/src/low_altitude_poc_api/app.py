from datetime import UTC, datetime
from typing import Literal

from fastapi import FastAPI, status
from pydantic import BaseModel
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


class TelemetryBatch(BaseModel):
    events: list[TelemetryEvent]


class IngestResult(BaseModel):
    accepted: int
    rejected: int


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


class SituationSnapshot(BaseModel):
    drones: list[DroneSnapshot]


def create_app(database_url: str = "sqlite:///./low_altitude_poc.db") -> FastAPI:
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    app = FastAPI(title="无人机低空智慧调度平台 POC")

    @app.post(
        "/api/telemetry/batches",
        response_model=IngestResult,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def ingest_telemetry(batch: TelemetryBatch) -> IngestResult:
        with Session(engine) as session:
            for event in batch.events:
                state = session.get(LatestDroneState, event.drone_id)
                values = event.model_dump(mode="json", exclude={"event_id"})
                values["platform_received_time"] = (
                    datetime.now(UTC).isoformat().replace("+00:00", "Z")
                )
                if state is None:
                    session.add(LatestDroneState(**values))
                else:
                    for field, value in values.items():
                        setattr(state, field, value)
            session.commit()
        return IngestResult(accepted=len(batch.events), rejected=0)

    @app.get("/api/situation/snapshot", response_model=SituationSnapshot)
    def get_situation_snapshot() -> SituationSnapshot:
        with Session(engine) as session:
            states = session.scalars(
                select(LatestDroneState).order_by(LatestDroneState.drone_id)
            ).all()
            return SituationSnapshot(
                drones=[
                    DroneSnapshot.model_validate(state, from_attributes=True)
                    for state in states
                ]
            )

    return app
