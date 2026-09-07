from typing import Any

from pydantic import BaseModel, ConfigDict


class Confirmation(BaseModel):
    confirmed: bool = False


class MissionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    status: str | None = None
    progress: int | None = None
    overdue: bool | None = None


class DemoState(BaseModel):
    snapshotVersion: str
    schemaVersion: int
    simulationClock: str
    drones: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    ledgers: list[dict[str, Any]]
    audit: list[dict[str, Any]]


class ResetResult(BaseModel):
    snapshotVersion: str
    businessState: dict[str, Any]
    auditEntry: dict[str, Any]
