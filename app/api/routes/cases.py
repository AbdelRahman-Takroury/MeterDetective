"""Initial case inventory endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CaseDetailResponse, CaseListResponse, CaseSummary, CaseTraceResponse
from app.db import models
from app.db.repositories import CaseRepository
from app.db.session import get_db

router = APIRouter(prefix="/cases", tags=["cases"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=CaseListResponse)
def list_cases(
    db: DbSession,
    status: Annotated[str | None, Query(min_length=1, max_length=30)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CaseListResponse:
    items = CaseRepository(db).list(status=status, offset=offset, limit=limit)
    return CaseListResponse(items=items, offset=offset, limit=limit)


@router.get("/{case_id}", response_model=CaseDetailResponse)
def case_detail(case_id: str, db: DbSession) -> CaseDetailResponse:
    try:
        from uuid import UUID

        parsed_id = UUID(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found") from exc
    case = CaseRepository(db).get(parsed_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    meter_ids = list(
        db.scalars(
            select(models.CaseMeter.meter_id)
            .where(models.CaseMeter.case_id == parsed_id)
            .order_by(models.CaseMeter.meter_id)
        )
    )
    evidence = list(
        db.scalars(
            select(models.Evidence)
            .where(models.Evidence.case_id == parsed_id)
            .order_by(models.Evidence.created_at, models.Evidence.id)
        )
    )
    hypotheses = list(
        db.scalars(
            select(models.Hypothesis)
            .where(models.Hypothesis.case_id == parsed_id)
            .order_by(models.Hypothesis.confidence.desc(), models.Hypothesis.label)
        )
    )
    reports = list(
        db.scalars(
            select(models.InvestigationReport)
            .where(models.InvestigationReport.case_id == parsed_id)
            .order_by(models.InvestigationReport.version.desc())
            .limit(1)
        )
    )
    events = list(
        db.scalars(
            select(models.CaseEvent)
            .where(models.CaseEvent.case_id == parsed_id)
            .order_by(models.CaseEvent.created_at, models.CaseEvent.id)
        )
    )
    return CaseDetailResponse(
        case=CaseSummary.model_validate(case),
        meter_ids=meter_ids,
        evidence=[
            {
                "id": str(item.id),
                "kind": item.kind,
                "source": item.source,
                "value": item.value_json,
                "reliability": item.reliability,
                "created_at": item.created_at,
            }
            for item in evidence
        ],
        hypotheses=[
            {
                "id": str(item.id),
                "label": item.label,
                "confidence": item.confidence,
                "supporting_evidence": item.support_json,
                "contradicting_evidence": item.contradiction_json,
                "update_history": item.update_history_json,
                "updated_at": item.updated_at,
            }
            for item in hypotheses
        ],
        latest_report=(
            {
                "id": str(reports[0].id),
                "version": reports[0].version,
                "status": reports[0].status,
                "answers": reports[0].answers_json,
                "completeness": reports[0].completeness,
                "generated_at": reports[0].generated_at,
            }
            if reports
            else None
        ),
        case_events=[
            {
                "id": str(item.id),
                "event_type": item.event_type,
                "details": item.details_json,
                "created_at": item.created_at,
            }
            for item in events
        ],
    )


@router.get("/{case_id}/trace", response_model=CaseTraceResponse)
def case_trace(case_id: str, db: DbSession) -> CaseTraceResponse:
    try:
        from uuid import UUID

        parsed_id = UUID(case_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found") from exc
    if CaseRepository(db).get(parsed_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    runs = list(
        db.scalars(
            select(models.AgentRun)
            .where(models.AgentRun.case_id == parsed_id)
            .order_by(models.AgentRun.started_at, models.AgentRun.id)
        )
    )
    payload = []
    for run in runs:
        executions = list(
            db.scalars(
                select(models.ToolExecution)
                .where(models.ToolExecution.run_id == run.id)
                .order_by(models.ToolExecution.created_at, models.ToolExecution.id)
            )
        )
        payload.append(
            {
                "id": str(run.id),
                "status": run.status,
                "trigger": run.trigger,
                "state": run.state_json,
                "started_at": run.started_at,
                "ended_at": run.ended_at,
                "tools": [
                    {
                        "id": str(item.id),
                        "name": item.tool_name,
                        "status": item.status,
                        "input": item.input_json,
                        "output": item.output_json,
                        "latency_ms": item.latency_ms,
                        "error": item.error,
                    }
                    for item in executions
                ],
            }
        )
    return CaseTraceResponse(case_id=parsed_id, runs=payload)
