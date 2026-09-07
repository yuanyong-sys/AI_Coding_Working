from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.domains.audit.models import Audit
from app.domains.demo import service
from app.domains.demo.schemas import Confirmation, DemoState, MissionPatch, ResetResult
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
    for field, value in changes.model_dump(exclude_none=True).items():
        setattr(mission, field, value)
    await session.commit()
    await session.refresh(mission)
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
