"""Deterministic reading replay and investigation trigger."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import ReplayStepRequest, ReplayStepResponse, ScenarioResetResponse
from app.db.session import get_db
from app.services.investigation import InvestigationFailure, InvestigationService, ReplayCommand
from app.services.scenario_one import (
    EVENT_TIME,
    REPAIR_END,
    REPAIR_START,
    SCENARIO_ID,
    TARGET_METER,
    ScenarioOneService,
)

router = APIRouter(prefix="/replay", tags=["replay"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("/reset", response_model=ScenarioResetResponse)
def reset_scenario_one(db: DbSession) -> ScenarioResetResponse:
    """Replace only the synthetic Scenario 1 slice and run its investigation."""
    try:
        result, removed = ScenarioOneService(db).reset_and_run()
        if result.case_id is None or result.report_id is None:
            raise InvestigationFailure("Scenario 1 did not create a report")
        db.commit()
    except (InvestigationFailure, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return ScenarioResetResponse(
        scenario_id=SCENARIO_ID,
        meter_id=TARGET_METER,
        event_time=EVENT_TIME,
        case_id=result.case_id,
        report_id=result.report_id,
        run_id=result.run_id,
        repair_window_start=REPAIR_START,
        repair_window_end=REPAIR_END,
        reset_records=removed,
    )


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
