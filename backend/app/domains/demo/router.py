from fastapi import APIRouter, Depends, HTTPException, Request, Response
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.domains.audit.models import Audit
from app.domains.demo import service
from app.domains.demo.schemas import (
    BatchDispatchRequest,
    AnomalyRequest,
    Confirmation,
    ControlRequest,
    DemoControlRequest,
    DemoControlState,
    SimulationFailure,
    DemoState,
    DispatchRequest,
    FalsePositiveRequest,
    ReminderRequest,
    ResolveAlertRequest,
    ReportExportRequest,
    TransferRequest,
    MissionDraft,
    MissionPatch,
    MissionStatus,
    ResetResult,
    TransitionRequest,
)
from app.domains.demo.snapshot import SNAPSHOT_VERSION
from app.domains.mission.models import Mission
from app.domains.alert.models import Alert
from app.domains.report.service import build_pdf, build_xlsx, report_lines


router = APIRouter(prefix="/api", tags=["poc-demo"])


def demo_failure(request: Request, kind: str, code: str, reason: str) -> dict:
    return {
        "code": code, "reason": reason,
        "lastValid": request.app.state.demo_control.simulationClock,
        "retry": f"/api/demo/control/retry/{kind}",
    }


async def require_running_mission(session: AsyncSession, mission_id: str) -> Mission:
    mission = await service.get_transitionable_mission(session, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if mission.status != "RUNNING":
        raise HTTPException(status_code=409, detail="MISSION_NOT_RUNNING")
    return mission


@router.get("/state", response_model=DemoState)
async def state(request: Request, session: AsyncSession = Depends(get_session)) -> dict:
    if request.app.state.demo_control.failures.data:
        raise HTTPException(status_code=503, detail={
            "code": "DEMO_DATA_UNAVAILABLE", "reason": "演示数据服务加载失败",
            "lastValid": request.app.state.demo_control.simulationClock,
            "retry": "/api/demo/control/retry/data",
        })
    result = await service.read_state(session)
    result["simulationClock"] = request.app.state.demo_control.simulationClock
    return result


@router.get("/demo/control")
async def get_demo_control(request: Request) -> DemoControlState:
    return request.app.state.demo_control


@router.post("/demo/control")
async def set_demo_control(
    request: Request, control: DemoControlRequest, session: AsyncSession = Depends(get_session)
) -> DemoControlState:
    state: DemoControlState = request.app.state.demo_control
    current = datetime.fromisoformat(state.simulationClock)
    state.simulationClock = (current + timedelta(minutes=control.advanceMinutes)).isoformat()
    for failure in control.failures:
        setattr(state.failures, failure.value, True)
    initial = datetime.fromisoformat(DemoControlState().simulationClock)
    elapsed_minutes = int((datetime.fromisoformat(state.simulationClock) - initial).total_seconds() // 60)
    if elapsed_minutes:
        await service.trigger_emergency_reminders(session, elapsed_minutes)
    return state


@router.post("/demo/control/retry/{failure}")
async def retry_demo_failure(request: Request, failure: SimulationFailure) -> DemoControlState:
    setattr(request.app.state.demo_control.failures, failure.value, False)
    return request.app.state.demo_control


@router.get("/map/status")
async def map_status(request: Request) -> dict:
    if request.app.state.demo_control.failures.map:
        raise HTTPException(status_code=503, detail=demo_failure(
            request, "map", "DEMO_MAP_UNAVAILABLE", "地图底图服务不可用"
        ))
    return {"status": "AVAILABLE", "lastValid": request.app.state.demo_control.simulationClock}


@router.get("/audit")
async def audit(session: AsyncSession = Depends(get_session)) -> dict:
    return {"audit": (await service.read_state(session))["audit"]}


@router.post("/reports/export")
async def export_report(request: ReportExportRequest, session: AsyncSession = Depends(get_session)) -> Response:
    generated_at = datetime.now(timezone.utc).isoformat()
    detail = f"{request.template}/{request.format}；筛选={request.filters}；字段={request.fields}"
    if request.simulateFailure:
        await service.record_generic_audit(session, action="REPORT_EXPORTED", result="FAILED", detail=detail, actor=request.operator)
        raise HTTPException(status_code=503, detail="SIMULATED_EXPORT_FAILURE")
    lines = report_lines(request, generated_at)
    content = build_xlsx(request, lines) if request.format == "xlsx" else build_pdf(lines)
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if request.format == "xlsx" else "application/pdf"
    filename = f"inspection-report.{request.format}"
    await service.record_generic_audit(session, action="REPORT_EXPORTED", result="SUCCESS", detail=detail, actor=request.operator)
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/alerts")
async def alerts(
    level: str | None = None,
    time: str = "all",
    keyword: str = "",
    session: AsyncSession = Depends(get_session),
) -> dict:
    return {"alerts": await service.filter_alerts(session, level=level, time_window=time, keyword=keyword)}


@router.get("/alerts/{alert_id}")
async def alert_detail(alert_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    return await service.alert_detail(session, alert)


@router.get("/alerts/{alert_id}/evidence")
async def alert_evidence(
    alert_id: str, request: Request, simulateFailure: bool = False, session: AsyncSession = Depends(get_session)
) -> dict:
    if await session.get(Alert, alert_id) is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if request.app.state.demo_control.failures.media:
        raise HTTPException(status_code=503, detail=demo_failure(
            request, "media", "DEMO_MEDIA_STREAM_INTERRUPTED", "视频证据流已中断"
        ))
    if simulateFailure:
        raise HTTPException(status_code=503, detail="EVIDENCE_TEMPORARILY_UNAVAILABLE")
    return service.alert_evidence(alert_id)


@router.post("/alerts/{alert_id}/confirm")
async def confirm_alert(alert_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if alert.status not in {"PENDING_VERIFICATION", "PENDING"}:
        raise HTTPException(status_code=409, detail="ALERT_NOT_PENDING")
    alert.status = "PROCESSING"
    await service.record_alert_audit(
        session, alert, action="ALERT_CONFIRMED", detail="人工复核确认告警有效",
        before_state="PENDING_VERIFICATION", after_state="PROCESSING",
    )
    return await service.alert_detail(session, alert)


@router.post("/alerts/{alert_id}/false-positive")
async def mark_false_positive(
    alert_id: str, request: FalsePositiveRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if alert.status in {"RESOLVED", "FALSE_POSITIVE"}:
        raise HTTPException(status_code=409, detail="ALERT_ALREADY_CLOSED")
    previous = alert.status
    alert.status = "FALSE_POSITIVE"
    await service.record_alert_audit(
        session, alert, action="ALERT_MARKED_FALSE_POSITIVE",
        detail="误报原因：" + "、".join(request.reasons),
        before_state=previous, after_state="FALSE_POSITIVE",
    )
    return await service.alert_detail(session, alert)


@router.post("/alerts/emergency-reminders")
async def emergency_reminders(
    request: ReminderRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    return {"alerts": await service.trigger_emergency_reminders(session, request.elapsedMinutes)}


@router.post("/alerts/{alert_id}/transfer")
async def transfer_alert(
    alert_id: str, request: TransferRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    alert = await service.get_actionable_alert(session, alert_id)
    transfer_id = f"ZP-MOCK-{alert.id.rsplit('-', 1)[-1]}"
    previous = alert.status
    alert.status = "PROCESSING"
    await service.record_alert_audit(
        session, alert, action="ALERT_TRANSFERRED",
        detail=f"模拟转派至{request.target}，转派单号 {transfer_id}",
        before_state=previous, after_state="PROCESSING",
    )
    result = await service.alert_detail(session, alert)
    result["transferId"] = transfer_id
    return result


@router.post("/alerts/{alert_id}/escalate")
async def escalate_alert(
    alert_id: str, confirmation: Confirmation, session: AsyncSession = Depends(get_session)
) -> dict:
    if not confirmation.confirmed:
        raise HTTPException(status_code=409, detail="CONFIRMATION_REQUIRED")
    alert = await service.get_actionable_alert(session, alert_id)
    event_id = f"SJ-MOCK-{alert.id.rsplit('-', 1)[-1]}"
    previous = alert.status
    alert.status = "PROCESSING"
    await service.record_alert_audit(
        session, alert, action="ALERT_ESCALATED", detail=f"生成模拟事件编号 {event_id}",
        before_state=previous, after_state="PROCESSING",
    )
    result = await service.alert_detail(session, alert)
    result["eventId"] = event_id
    return result


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str, request: ResolveAlertRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    alert = await service.get_actionable_alert(session, alert_id)
    previous = alert.status
    alert.status = "RESOLVED"
    await service.record_alert_audit(
        session, alert, action="ALERT_RESOLVED", detail=f"办结结果：{request.result}",
        before_state=previous, after_state="RESOLVED",
    )
    return await service.alert_detail(session, alert)


@router.patch("/tasks/{mission_id}")
async def patch_mission(
    mission_id: str, changes: MissionPatch, session: AsyncSession = Depends(get_session)
) -> dict:
    mission = await session.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if changes.status is not None and not service.can_transition(mission.status, changes.status):
        raise HTTPException(status_code=409, detail="INVALID_STATE_TRANSITION")
    for field, value in changes.model_dump(exclude_none=True).items():
        setattr(mission, field, value)
    await session.commit()
    await session.refresh(mission)
    return service.serialize_mission(mission)


@router.get("/tasks/{mission_id}/monitor")
async def monitor_mission(mission_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    mission = await require_running_mission(session, mission_id)
    return service.monitor_snapshot(mission)


@router.post("/tasks/{mission_id}/control")
async def control_mission(
    mission_id: str, control: ControlRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    mission = await require_running_mission(session, mission_id)
    command = control.command.value
    injected_timeout = request.app.state.demo_control.failures.control
    result = "FAILED" if control.simulateFailure or injected_timeout else "SUCCESS"
    previous_control_state = mission.control_state
    mission.control_state = command if result == "SUCCESS" else mission.control_state
    await service.record_audit(
        session, action=f"MISSION_CONTROL_{command}", mission_id=mission.id,
        result=result, detail=f"模拟{command}指令",
        before_state=previous_control_state, after_state=mission.control_state,
        failure_reason="模拟控制超时" if injected_timeout else "模拟指令失败" if control.simulateFailure else None,
    )
    if injected_timeout:
        raise HTTPException(status_code=504, detail=demo_failure(
            request, "control", "DEMO_CONTROL_TIMEOUT", "模拟飞控指令响应超时"
        ))
    if control.simulateFailure:
        raise HTTPException(status_code=503, detail="SIMULATED_CONTROL_FAILURE")
    return {"missionId": mission.id, "command": command, "result": result, "simulated": True}


@router.post("/tasks/{mission_id}/anomaly")
async def mission_anomaly(
    mission_id: str, request: AnomalyRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    mission = await require_running_mission(session, mission_id)
    kind = request.kind.value
    return await service.handle_mission_anomaly(session, mission, kind)


@router.post("/tasks/validate")
async def validate_mission(draft: MissionDraft, session: AsyncSession = Depends(get_session)) -> dict:
    return await service.validate_mission(session, draft)


@router.post("/tasks", status_code=201)
async def create_mission(draft: MissionDraft, session: AsyncSession = Depends(get_session)) -> dict:
    result = await service.validate_mission(session, draft)
    if not result["canDispatch"]:
        raise HTTPException(status_code=422, detail=result["blockers"])
    mission = await service.create_mission(session, draft)
    return service.serialize_mission(mission)


@router.post("/tasks/{mission_id}/transition")
async def transition_mission(
    mission_id: str, request: TransitionRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    mission = await service.get_transitionable_mission(session, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if not service.can_transition(mission.status, request.target):
        raise HTTPException(status_code=409, detail="INVALID_STATE_TRANSITION")
    previous = mission.status
    mission.status = request.target.value
    await service.record_audit(
        session, action="MISSION_STATUS_CHANGED", mission_id=mission.id,
        result="SUCCESS", detail=f"{previous}->{request.target.value}",
        before_state=previous, after_state=request.target.value,
    )
    if request.target == MissionStatus.TERMINATED:
        await service.record_audit(
            session, action="MISSION_DEMO_DATA_ARCHIVED", mission_id=mission.id,
            result="SUCCESS", detail="POC telemetry, media references and disposal trace archived",
            before_state=previous, after_state=request.target.value,
        )
    return service.serialize_mission(mission)


@router.post("/tasks/dispatch")
async def batch_dispatch(request: BatchDispatchRequest, session: AsyncSession = Depends(get_session)) -> dict:
    return await service.batch_dispatch(session, request.taskIds)


@router.post("/tasks/{mission_id}/dispatch")
async def dispatch_mission(
    mission_id: str, request: DispatchRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    mission = await service.get_dispatchable_mission(session, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if mission.status != "PENDING_DISPATCH":
        raise HTTPException(status_code=409, detail="INVALID_STATE")
    if request.simulateFailure:
        await service.record_audit(
            session, action="MISSION_DISPATCH", mission_id=mission.id,
            result="FAILED", detail="模拟下发失败", before_state=mission.status,
            after_state=mission.status, failure_reason="模拟下发失败",
        )
        raise HTTPException(status_code=503, detail="SIMULATED_DISPATCH_FAILURE")
    mission.status = "PENDING_EXECUTION"
    await service.record_audit(
        session, action="MISSION_DISPATCH", mission_id=mission.id,
        result="SUCCESS", detail="PENDING_DISPATCH->PENDING_EXECUTION",
        before_state="PENDING_DISPATCH", after_state="PENDING_EXECUTION",
    )
    return service.serialize_mission(mission)


@router.post("/demo/reset", response_model=ResetResult)
async def reset_demo(
    confirmation: Confirmation, request: Request, session: AsyncSession = Depends(get_session)
) -> dict:
    if not confirmation.confirmed:
        raise HTTPException(status_code=409, detail="CONFIRMATION_REQUIRED")
    audit_entry = await service.restore_business_state(session)
    request.app.state.demo_control = DemoControlState()
    full_state = await service.read_state(session)
    business_state = {key: value for key, value in full_state.items() if key != "audit"}
    return {
        "snapshotVersion": SNAPSHOT_VERSION,
        "businessState": business_state,
        "auditEntry": service.serialize_audit(audit_entry),
    }
