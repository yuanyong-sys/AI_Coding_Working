from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Engine, String, and_, distinct, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from low_altitude_poc_api.auth import AuthenticatedUser, AuthService


class QueryBase(DeclarativeBase):
    pass


class QueryTelemetryPoint(QueryBase):
    __tablename__ = "telemetry_points"

    source_type: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    drone_id: Mapped[str] = mapped_column(String)
    source_time: Mapped[str] = mapped_column(String)
    platform_received_time: Mapped[str] = mapped_column(String)


class QueryTelemetrySortie(QueryBase):
    __tablename__ = "telemetry_sorties"

    source_type: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    sortie_id: Mapped[str] = mapped_column(String)


class QueryIntent(StrEnum):
    DRONE_LIST = "drone_list"
    DRONE_COUNT = "drone_count"
    SORTIE_COUNT = "sortie_count"
    ONLINE_RATE = "online_rate"


class DataQueryRequest(BaseModel):
    question: str
    source_type: Literal["real", "simulated"] | None = None


class QueryPlan(BaseModel):
    intent: QueryIntent
    start_time: datetime
    end_time: datetime
    source_type: Literal["real", "simulated"] | None


class TimeRange(BaseModel):
    start: datetime
    end: datetime


class QueryFilters(BaseModel):
    role: str
    source_type: str


class QueryBasis(BaseModel):
    time_range: TimeRange
    data_sources: list[Literal["真实数据", "模拟数据"]]
    statistical_definition: str
    filters: QueryFilters


class TelemetryDetail(BaseModel):
    event_id: str
    drone_id: str
    source_time: datetime
    platform_received_time: datetime
    source_type: Literal["real", "simulated"]


class DetailEntry(BaseModel):
    record_type: Literal["无人机遥测"]
    drone_ids: list[str]
    records: list[TelemetryDetail]


class Visualization(BaseModel):
    type: Literal["map", "chart"]
    metric: str
    value: float | None = None
    drone_ids: list[str]


class DataQueryResponse(BaseModel):
    answer: str
    plan: QueryPlan
    query_basis: QueryBasis
    detail_entry: DetailEntry
    visualization: Visualization


def build_whitelisted_plan(
    question: str,
    source_type: Literal["real", "simulated"] | None,
    now: datetime,
) -> QueryPlan:
    normalized = "".join(question.split())
    if "在线率" in normalized:
        intent = QueryIntent.ONLINE_RATE
    elif "飞行架次" in normalized:
        intent = QueryIntent.SORTIE_COUNT
    elif "哪些无人机" in normalized or "无人机列表" in normalized:
        intent = QueryIntent.DRONE_LIST
    elif "多少架无人机" in normalized or "无人机数量" in normalized:
        intent = QueryIntent.DRONE_COUNT
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="当前仅支持无人机、飞行架次、在线率和时间范围相关的固定问题",
        )
    duration = (
        timedelta(minutes=30)
        if "最近30分钟" in normalized or "近30分钟" in normalized
        else timedelta(hours=24)
        if "最近24小时" in normalized or "近24小时" in normalized
        else timedelta(hours=1)
    )
    return QueryPlan(
        intent=intent,
        start_time=now - duration,
        end_time=now,
        source_type=source_type,
    )


def configure_data_query(
    app: FastAPI,
    engine: Engine,
    auth: AuthService,
    *,
    clock: Callable[[], datetime] | None = None,
) -> None:
    now = clock or (lambda: datetime.now(UTC))

    @app.post("/api/data-query", response_model=DataQueryResponse)
    def query_data(
        request: DataQueryRequest,
        user: AuthenticatedUser = Depends(auth.require_user),  # noqa: B008
    ) -> DataQueryResponse:
        if "query:read" not in user.capabilities:
            auth.record_audit(user.username, "data_query_denied", "denied")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        plan = build_whitelisted_plan(request.question, request.source_type, now())
        start = plan.start_time.isoformat().replace("+00:00", "Z")
        end = plan.end_time.isoformat().replace("+00:00", "Z")
        filters = [
            QueryTelemetryPoint.source_time >= start,
            QueryTelemetryPoint.source_time <= end,
        ]
        if plan.source_type:
            filters.append(QueryTelemetryPoint.source_type == plan.source_type)
        with Session(engine) as database:
            detail_records = list(
                database.scalars(
                    select(QueryTelemetryPoint)
                    .where(*filters)
                    .order_by(
                        QueryTelemetryPoint.source_time,
                        QueryTelemetryPoint.event_id,
                    )
                ).all()
            )
            drone_ids = list(
                database.scalars(
                    select(distinct(QueryTelemetryPoint.drone_id))
                    .where(*filters)
                    .order_by(QueryTelemetryPoint.drone_id)
                ).all()
            )
            if plan.intent == QueryIntent.SORTIE_COUNT:
                value = float(
                    database.scalar(
                        select(func.count(distinct(QueryTelemetrySortie.sortie_id)))
                        .select_from(QueryTelemetrySortie)
                        .join(
                            QueryTelemetryPoint,
                            and_(
                                QueryTelemetryPoint.source_type
                                == QueryTelemetrySortie.source_type,
                                QueryTelemetryPoint.event_id
                                == QueryTelemetrySortie.event_id,
                            ),
                        )
                        .where(*filters)
                    )
                    or 0
                )
                answer = f"该时间范围内共有 {int(value)} 个飞行架次。"
                definition = "按去重后的飞行架次标识统计"
            elif plan.intent == QueryIntent.ONLINE_RATE:
                latest_received = database.execute(
                    select(
                        QueryTelemetryPoint.drone_id,
                        func.max(QueryTelemetryPoint.platform_received_time),
                    )
                    .where(*filters)
                    .group_by(QueryTelemetryPoint.drone_id)
                ).all()
                online_cutoff = plan.end_time - timedelta(seconds=30)
                online = sum(
                    datetime.fromisoformat(received_at) >= online_cutoff
                    for _, received_at in latest_received
                )
                value = round(online / len(drone_ids) * 100, 1) if drone_ids else 0
                answer = f"该时间范围内已接入无人机在线率为 {value:.1f}%。"
                definition = (
                    "时间范围内最近平台接收时间距查询时刻不超过30秒的无人机占比"
                )
            else:
                value = float(len(drone_ids))
                if plan.intent == QueryIntent.DRONE_LIST:
                    names = "、".join(drone_ids) if drone_ids else "无"
                    answer = f"该时间范围内已接入无人机为：{names}。"
                    definition = "按无人机标识去重列出无人机遥测记录"
                else:
                    answer = f"该时间范围内共有 {len(drone_ids)} 架已接入无人机。"
                    definition = "按无人机标识去重统计无人机遥测记录"
            sources: list[Literal["真实数据", "模拟数据"]] = (
                ["真实数据" if plan.source_type == "real" else "模拟数据"]
                if plan.source_type
                else ["真实数据", "模拟数据"]
            )
            response = DataQueryResponse(
                answer=answer,
                plan=plan,
                query_basis=QueryBasis(
                    time_range=TimeRange(start=plan.start_time, end=plan.end_time),
                    data_sources=sources,
                    statistical_definition=definition,
                    filters=QueryFilters(
                        role=user.role_label,
                        source_type=(
                            "真实数据"
                            if plan.source_type == "real"
                            else "模拟数据"
                            if plan.source_type == "simulated"
                            else "全部来源"
                        ),
                    ),
                ),
                detail_entry=DetailEntry(
                    record_type="无人机遥测",
                    drone_ids=drone_ids,
                    records=[
                        TelemetryDetail.model_validate(record, from_attributes=True)
                        for record in detail_records
                    ],
                ),
                visualization=Visualization(
                    type=("map" if plan.intent == QueryIntent.DRONE_LIST else "chart"),
                    metric=plan.intent.value,
                    value=value,
                    drone_ids=drone_ids,
                ),
            )
            auth.record_audit(
                user.username,
                "data_query_executed",
                "allowed",
                subject=plan.intent.value,
            )
            return response
