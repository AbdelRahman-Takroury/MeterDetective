"""Paged queue projection; batch joins avoid per-case detail requests."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import CaseSummary, QueueItem, QueueResponse
from app.db import models
from app.db.session import get_db

router = APIRouter(prefix="/triage", tags=["triage"])


@router.get("/queue", response_model=QueueResponse)
def queue(
    db: Annotated[Session, Depends(get_db)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> QueueResponse:
    cases = list(db.scalars(
        select(models.Case).order_by(
            models.Case.priority_band.asc().nulls_last(),
            models.Case.active_rank.asc().nulls_last(),
            models.Case.triage_score.desc().nulls_last(),
            models.Case.opened_at, models.Case.id,
        ).offset(offset).limit(limit)
    ))
    ids = [case.id for case in cases]
    meters = {case_id: [] for case_id in ids}
    for link in db.scalars(select(models.CaseMeter).where(
        models.CaseMeter.case_id.in_(ids)
    ).order_by(models.CaseMeter.meter_id)):
        meters[link.case_id].append(link.meter_id)
    latest_versions = select(
        models.InvestigationReport.case_id,
        func.max(models.InvestigationReport.version).label("version"),
    ).where(models.InvestigationReport.case_id.in_(ids)).group_by(
        models.InvestigationReport.case_id
    ).subquery()
    reports = {report.case_id: report for report in db.scalars(
        select(models.InvestigationReport).join(latest_versions,
            (models.InvestigationReport.case_id == latest_versions.c.case_id)
            & (models.InvestigationReport.version == latest_versions.c.version))
    )}
    recommendations = {}
    for item in db.scalars(select(models.Recommendation).where(
        models.Recommendation.case_id.in_(ids)
    ).order_by(models.Recommendation.created_at, models.Recommendation.id)):
        recommendations[item.case_id] = item
    items = []
    for case in cases:
        report = reports.get(case.id)
        answers = {
            item.get("question_id"): item for item in (report.answers_json if report else [])
            if isinstance(item, dict)
        }
        def values(question: int, answers: dict = answers) -> dict:
            return answers.get(question, {}).get("structured_values", {}) or {}

        recommendation = recommendations.get(case.id)
        items.append(QueueItem(
            **CaseSummary.model_validate(case).model_dump(),
            meter_ids=meters[case.id],
            report_version=report.version if report else None,
            present_count=len(set(answers) & set(range(1, 19))),
            answered_count=sum(item.get("status") == "answered" for item in answers.values()),
            anomaly_type=values(1).get("anomaly_type"),
            revenue_jod=(values(16).get("revenue_at_risk_jod")
                         if answers.get(16, {}).get("status") == "answered" else None),
            precedent_count=(len(values(17).get("precedent_case_ids", []))
                             if answers.get(17, {}).get("status") == "answered" else None),
            recommendation_status=recommendation.status if recommendation else None,
        ))
    return QueueResponse(items=items, total=db.scalar(
        select(func.count()).select_from(models.Case)
    ) or 0, offset=offset, limit=limit)
