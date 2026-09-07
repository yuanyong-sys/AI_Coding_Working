from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.domains.audit.models import Audit
from app.domains.demo import service
from app.domains.demo.schemas import (
    BatchDispatchRequest,
    AnomalyRequest,
    Confirmation,
    ControlRequest,
    DemoState,
    DispatchRequest,
    MissionDraft,
    MissionPatch,
    ResetResult,
    TransitionRequest,
)
from app.domains.demo.snapshot import SNAPSHOT_VERSION
from app.domains.mission.models import Mission


router = APIRouter(prefix="/api", tags=["poc-demo"])


@router.get("/state", response_model=DemoState)
async def state(session: AsyncSession = Depends(get_session)) -> dict:
    return await service.read_state(session)


@router.get("/audit")
async def audit(session: AsyncSession = Depends(get_session)) -> dict:
    return {"audit": (await service.read_state(session))["audit"]}


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
    mission = await service.get_transitionable_mission(session, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if mission.status != "RUNNING":
        raise HTTPException(status_code=409, detail="MISSION_NOT_RUNNING")
    return service.monitor_snapshot(mission)


@router.post("/tasks/{mission_id}/control")
async def control_mission(
    mission_id: str, request: ControlRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    mission = await service.get_transitionable_mission(session, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if mission.status != "RUNNING":
        raise HTTPException(status_code=409, detail="MISSION_NOT_RUNNING")
    command = request.command.upper()
    if command not in {"HOVER", "RETURN"}:
        raise HTTPException(status_code=422, detail="UNSUPPORTED_CONTROL")
    result = "FAILED" if request.simulateFailure else "SUCCESS"
    mission.control_state = command if result == "SUCCESS" else mission.control_state
    await service.record_audit(
        session, action=f"MISSION_CONTROL_{command}", mission_id=mission.id,
        result=result, detail="模拟飞控指令",
    )
    if request.simulateFailure:
        raise HTTPException(status_code=503, detail="SIMULATED_CONTROL_FAILURE")
    return {"missionId": mission.id, "command": command, "result": result, "simulated": True}


@router.post("/tasks/{mission_id}/anomaly")
async def mission_anomaly(
    mission_id: str, request: AnomalyRequest, session: AsyncSession = Depends(get_session)
) -> dict:
    mission = await service.get_transitionable_mission(session, mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="NOT_FOUND")
    if mission.status != "RUNNING":
        raise HTTPException(status_code=409, detail="MISSION_NOT_RUNNING")
    kind = request.kind.upper()
    if kind not in {"LINK_LOSS", "LOW_BATTERY"}:
        raise HTTPException(status_code=422, detail="UNSUPPORTED_ANOMALY")
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
    mission.status = request.target
    await service.record_audit(
        session, action="MISSION_STATUS_CHANGED", mission_id=mission.id,
        result="SUCCESS", detail=f"{previous}->{request.target}",
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
            result="FAILED", detail="模拟下发失败",
        )
        raise HTTPException(status_code=503, detail="SIMULATED_DISPATCH_FAILURE")
    mission.status = "PENDING_EXECUTION"
    await service.record_audit(
        session, action="MISSION_DISPATCH", mission_id=mission.id,
        result="SUCCESS", detail="PENDING_DISPATCH->PENDING_EXECUTION",
    )
    return service.serialize_mission(mission)


@router.post("/demo/reset", response_model=ResetResult)
async def reset_demo(
    confirmation: Confirmation, session: AsyncSession = Depends(get_session)
) -> dict:
    if not confirmation.confirmed:
        raise HTTPException(status_code=409, detail="CONFIRMATION_REQUIRED")
    audit_entry = await service.restore_business_state(session)
    full_state = await service.read_state(session)
    business_state = {key: value for key, value in full_state.items() if key != "audit"}
    return {
        "snapshotVersion": SNAPSHOT_VERSION,
        "businessState": business_state,
        "auditEntry": service.serialize_audit(audit_entry),
    }
