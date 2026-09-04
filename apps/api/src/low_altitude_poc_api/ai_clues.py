from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Engine, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from low_altitude_poc_api.auth import AuthenticatedUser, AuthService

logger = logging.getLogger(__name__)


class ClueBase(DeclarativeBase):
    pass


class AIClueRecord(ClueBase):
    __tablename__ = "ai_anomaly_clues"

    clue_id: Mapped[str] = mapped_column(String, primary_key=True)
    payload: Mapped[str] = mapped_column(String)
    review_status: Mapped[str] = mapped_column(String)


class ClueLocation(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class InferenceResult(BaseModel):
    result_id: str
    anomaly_type: Literal[
        "suspected_traffic_accident",
        "suspected_fire",
        "suspected_crowd",
    ]
    confidence: float = Field(ge=0, le=1)
    source_time: datetime
    location: ClueLocation
    material_reference: str
    model_version: str
    source_type: Literal["simulated", "evaluation"]

    @field_validator("material_reference")
    @classmethod
    def controlled_relative_reference(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("must be a controlled relative reference")
        return value


class AIClue(InferenceResult):
    clue_id: str
    review_status: Literal["pending_review"]


class AIClueCreated(BaseModel):
    clue_id: str
    status: Literal["pending_review"]


class AIClueList(BaseModel):
    clues: list[AIClue]


def configure_ai_clues(
    app: FastAPI,
    engine: Engine,
    auth: AuthService,
    *,
    inference_token: str | None = None,
    material_root: Path | None = None,
) -> None:
    ClueBase.metadata.create_all(engine)
    configured_token = inference_token or os.getenv("LOW_ALTITUDE_INFERENCE_TOKEN")
    if configured_token is None:
        configured_token = secrets.token_urlsafe(24)
        logger.warning("Generated local inference adapter token: %s", configured_token)
    root = (
        material_root
        or Path(os.getenv("LOW_ALTITUDE_CLUE_MATERIAL_ROOT", "var/clue-materials"))
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)

    def require_clue_reviewer(
        user: AuthenticatedUser = Depends(auth.require_user),  # noqa: B008
    ) -> AuthenticatedUser:
        if "clue:review" not in user.capabilities:
            auth.record_audit(user.username, "ai_clue_access_denied", "denied")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return user

    @app.post(
        "/api/inference/results",
        response_model=AIClueCreated,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def accept_inference_result(
        result: InferenceResult,
        adapter_token: Annotated[str | None, Header(alias="X-Inference-Token")] = None,
    ) -> AIClueCreated:
        if not secrets.compare_digest(adapter_token or "", configured_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        material_path = (root / result.material_reference).resolve()
        if not material_path.is_relative_to(root) or not material_path.is_file():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="material reference is not present in the controlled directory",
            )
        with Session(engine) as database:
            if database.get(AIClueRecord, result.result_id) is None:
                database.add(
                    AIClueRecord(
                        clue_id=result.result_id,
                        payload=result.model_dump_json(),
                        review_status="pending_review",
                    )
                )
                database.commit()
        return AIClueCreated(clue_id=result.result_id, status="pending_review")

    @app.get("/api/ai-clues", response_model=AIClueList)
    def list_ai_clues(
        _user: AuthenticatedUser = Depends(require_clue_reviewer),  # noqa: B008
    ) -> AIClueList:
        with Session(engine) as database:
            records = database.scalars(
                select(AIClueRecord).order_by(AIClueRecord.clue_id)
            ).all()
            return AIClueList(
                clues=[
                    AIClue(
                        **json.loads(record.payload),
                        clue_id=record.clue_id,
                        review_status="pending_review",
                    )
                    for record in records
                ]
            )

    @app.get("/api/ai-clues/{clue_id}/material")
    def get_clue_material(
        clue_id: str,
        _user: AuthenticatedUser = Depends(require_clue_reviewer),  # noqa: B008
    ) -> FileResponse:
        with Session(engine) as database:
            record = database.get(AIClueRecord, clue_id)
            if record is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            result = InferenceResult.model_validate_json(record.payload)
        material_path = (root / result.material_reference).resolve()
        if not material_path.is_relative_to(root) or not material_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(material_path)
