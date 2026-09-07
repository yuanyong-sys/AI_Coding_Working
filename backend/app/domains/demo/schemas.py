from __future__ import annotations

from typing import Any
from datetime import date as Date
from datetime import time as Time

from pydantic import BaseModel, ConfigDict


class Confirmation(BaseModel):
    confirmed: bool = False


class MissionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    status: str | None = None
    progress: int | None = None
    overdue: bool | None = None


class MissionDraft(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    start: Time
    end: Time
    droneId: str
    battery: int
    route: str
    aiItems: list[str]
    date: Date
    type: str | None = None
    priority: str | None = None
    owner: str | None = None
    area: str | None = None
    dock: str | None = None
    backupDrone: str | None = None


class DispatchRequest(BaseModel):
    simulateFailure: bool = False


class BatchDispatchRequest(BaseModel):
    taskIds: list[str]


class TransitionRequest(BaseModel):
    target: str


class ControlRequest(BaseModel):
    command: str
    simulateFailure: bool = False


class AnomalyRequest(BaseModel):
    kind: str


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
