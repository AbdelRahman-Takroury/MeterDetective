"""Deterministic reading replay and investigation trigger."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import ReplayStepRequest, ReplayStepResponse
from app.db.session import get_db
from app.services.investigation import InvestigationFailure, InvestigationService, ReplayCommand

router = APIRouter(prefix="/replay", tags=["replay"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/step", response_model=ReplayStepResponse)
def replay_step(request: ReplayStepRequest, db: DbSession) -> ReplayStepResponse:
    if request.event_time.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="event_time must include a timezone",
        )
    try:
        result = InvestigationService(db).replay(
            ReplayCommand(
                meter_id=request.meter_id,
                event_time=request.event_time,
                lookback_days=request.lookback_days,
                idempotency_key=request.idempotency_key,
            )
        )
        db.commit()
    except (InvestigationFailure, ValueError) as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return ReplayStepResponse.model_validate(result.model_dump())
