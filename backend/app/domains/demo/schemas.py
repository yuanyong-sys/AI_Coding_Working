from __future__ import annotations

from typing import Any
from enum import StrEnum
from datetime import date as Date
from datetime import time as Time

from pydantic import BaseModel, ConfigDict, Field


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


class MissionStatus(StrEnum):
    PENDING_DISPATCH = "PENDING_DISPATCH"
    PENDING_EXECUTION = "PENDING_EXECUTION"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    ABNORMAL = "ABNORMAL"
    TERMINATED = "TERMINATED"


class ControlCommand(StrEnum):
    HOVER = "HOVER"
    RETURN = "RETURN"


class AnomalyKind(StrEnum):
    LINK_LOSS = "LINK_LOSS"
    LOW_BATTERY = "LOW_BATTERY"


class TransitionRequest(BaseModel):
    target: MissionStatus


class ControlRequest(BaseModel):
    command: ControlCommand
    simulateFailure: bool = False


class AnomalyRequest(BaseModel):
    kind: AnomalyKind


class FalsePositiveRequest(BaseModel):
    reasons: list[str] = Field(min_length=1)


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
