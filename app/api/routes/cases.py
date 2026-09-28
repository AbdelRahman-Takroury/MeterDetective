"""Initial case inventory endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import CaseDetailResponse, CaseListResponse, CaseSummary, CaseTraceResponse
from app.db import models
from app.db.repositories import CaseRepository
from app.db.session import get_db
from app.services.scenario_one import (
    REPAIR_END,
    REPAIR_START,
    SCENARIO_ID,
    TARGET_METER,
)

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
    recommendations = list(
        db.scalars(
            select(models.Recommendation)
            .where(models.Recommendation.case_id == parsed_id)
            .order_by(models.Recommendation.created_at, models.Recommendation.id)
        )
    )
    approvals_by_recommendation = {
        item.recommendation_id: item
        for item in db.scalars(
            select(models.Approval).where(
                models.Approval.recommendation_id.in_([item.id for item in recommendations])
            )
        )
    }
    actions = list(
        db.scalars(
            select(models.Action)
            .where(models.Action.case_id == parsed_id)
            .order_by(models.Action.created_at, models.Action.id)
        )
    )
    report = reports[0] if reports else None
    answers = {
        item.get("question_id"): item
        for item in (report.answers_json if report else [])
        if isinstance(item, dict) and isinstance(item.get("question_id"), int)
    }
    question_ids = sorted(set(answers) & set(range(1, 19)))
    financial = db.scalar(
        select(models.FinancialImpact)
        .join(models.InvestigationReport)
        .where(models.FinancialImpact.case_id == parsed_id)
        .order_by(models.InvestigationReport.version.desc())
        .limit(1)
    ) if report else None
    tariff = db.get(models.Tariff, financial.tariff_id) if financial else None
    triage = db.scalar(
        select(models.TriageAssessment)
        .join(models.InvestigationReport)
        .where(models.TriageAssessment.case_id == parsed_id)
        .order_by(models.InvestigationReport.version.desc())
        .limit(1)
    ) if report else None

    precedent_ids = []
    if answer := answers.get(17):
        precedent_ids = (answer.get("structured_values") or {}).get("precedent_case_ids", [])
    precedent_rows = []
    for raw_id in precedent_ids:
        try:
            precedent_id = UUID(str(raw_id))
        except ValueError:
            continue
        precedent = db.get(models.Case, precedent_id)
        if precedent is None:
            continue
        exact = bool(db.scalar(select(models.CaseMeter).where(
            models.CaseMeter.case_id == precedent_id,
            models.CaseMeter.meter_id.in_(meter_ids),
        )))
        precedent_rows.append({
            "case_id": str(precedent.id),
            "title": precedent.title,
            "status": precedent.status,
            "outcome": precedent.status if precedent.status in {"closed", "resolved"} else None,
            "match_type": "exact_meter" if exact else "similar_system",
            "updated_at": precedent.updated_at,
        })

    citations_by_url: dict[str, dict] = {}
    def collect_citations(value):  # noqa: ANN001, ANN202 - recursive JSON traversal
        if isinstance(value, list):
            for item in value:
                collect_citations(item)
        elif isinstance(value, dict):
            url = value.get("source_url")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                citations_by_url[url] = {
                    "source_url": url,
                    "title": value.get("title") or value.get("document_title") or url,
                    "license": value.get("license"),
                    "section": value.get("section") or value.get("page_or_section"),
                    "relevance": value.get("relevance") or value.get("score"),
                }
            for nested in value.values():
                collect_citations(nested)
    collect_citations([item.value_json for item in evidence])
    collect_citations(report.answers_json if report else [])
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
        recommendations=[
            {
                "id": str(item.id),
                "action_type": item.action_type,
                "rationale": item.rationale,
                "risk": item.risk,
                "requires_approval": item.requires_approval,
                "status": item.status,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "approval": (
                    {
                        "id": str(approvals_by_recommendation[item.id].id),
                        "decision": approvals_by_recommendation[item.id].decision,
                        "decided_by": approvals_by_recommendation[item.id].decided_by,
                        "decided_at": approvals_by_recommendation[item.id].decided_at,
                        "comment": approvals_by_recommendation[item.id].comment,
                    }
                    if item.id in approvals_by_recommendation
                    else None
                ),
            }
            for item in recommendations
        ],
        actions=[
            {
                "id": str(item.id),
                "recommendation_id": (
                    str(item.recommendation_id) if item.recommendation_id else None
                ),
                "action_type": item.action_type,
                "status": item.status,
                "result": item.result_json,
                "created_at": item.created_at,
                "executed_at": item.executed_at,
            }
            for item in actions
        ],
        report_coverage={
            "has_report": report is not None,
            "legacy_or_imported": report is None,
            "present_question_ids": question_ids,
            "missing_question_ids": sorted(set(range(1, 19)) - set(question_ids)),
            "present_count": len(question_ids),
            "answered_count": sum(
                item.get("status") == "answered" for item in answers.values()
            ),
        },
        financial_impact=(
            {
                "missing_kwh": {
                    "low": financial.missing_kwh_low,
                    "base": financial.missing_kwh_base,
                    "high": financial.missing_kwh_high,
                },
                "risk_jod": {
                    "low": financial.risk_jod_low,
                    "base": financial.risk_jod_base,
                    "high": financial.risk_jod_high,
                },
                "confidence": financial.confidence,
                "source_report_id": str(financial.report_id),
                "assumptions": financial.assumptions_json,
                "tariff": {
                    "id": str(tariff.id),
                    "name": tariff.name,
                    "currency": tariff.currency,
                    "jod_per_kwh": tariff.jod_per_kwh,
                    "source": tariff.source,
                    "is_synthetic": tariff.is_synthetic,
                } if tariff else None,
            }
            if financial else None
        ),
        triage_assessment=(
            {
                "score": triage.score,
                "band": triage.band,
                "active_rank": triage.active_rank,
                "active_count": triage.active_count,
                "percentile": triage.percentile,
                "factors": triage.factors_json,
                "policy_version": triage.policy_version,
                "calculated_at": triage.calculated_at,
                "source_report_id": str(triage.report_id),
            }
            if triage else None
        ),
        precedents=precedent_rows,
        citations=list(citations_by_url.values()),
        scenario=(
            {
                "id": SCENARIO_ID,
                "target_meter": TARGET_METER,
                "repair_window_start": REPAIR_START,
                "repair_window_end": REPAIR_END,
            }
            if TARGET_METER in meter_ids else None
        ),
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
