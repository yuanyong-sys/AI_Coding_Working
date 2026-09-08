from __future__ import annotations

from typing import Any, Literal
from enum import StrEnum
from datetime import date as Date
from datetime import time as Time

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("reasons")
    @classmethod
    def reasons_must_contain_text(cls, reasons: list[str]) -> list[str]:
        cleaned = [reason.strip() for reason in reasons if reason.strip()]
        if not cleaned:
            raise ValueError("至少选择一个误报原因")
        return cleaned


class TransferRequest(BaseModel):
    target: str = Field(min_length=1)

    @field_validator("target")
    @classmethod
    def target_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("转派目标不能为空")
        return value.strip()


class ResolveAlertRequest(BaseModel):
    result: str = Field(min_length=1)

    @field_validator("result")
    @classmethod
    def result_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("办结结果不能为空")
        return value.strip()


class ReminderRequest(BaseModel):
    elapsedMinutes: int = Field(ge=0)


class ReportExportRequest(BaseModel):
    template: Literal["日报", "周报", "月报", "自定义"]
    format: Literal["xlsx", "pdf"]
    fields: list[str] = Field(min_length=1)
    filters: dict[str, Any]
    stats: dict[str, str]
    rows: list[dict[str, Any]]
    analysisDimensions: dict[str, list[str]] = Field(default_factory=dict)
    operator: str = "王警官"
    simulateFailure: bool = False


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
