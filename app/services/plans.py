"""Version operational plans only when reliable evidence changes their scope."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

POLICY_VERSION = "evidence-scope-v1"
GOALS = {
    "local": "Investigate an individual meter problem.",
    "shared": "Investigate a shared transformer or network problem.",
    "unknown": "Gather enough reliable evidence to determine the affected area.",
}
STEPS = {
    "local": ["Review the affected meter and its connections.",
              "Request operator approval before a simulated inspection.",
              "Check follow-up readings for recovery."],
    "shared": ["Review the shared transformer and affected meters.",
               "Compare transformer input with the meter totals.",
               "Request operator approval before a simulated inspection.",
               "Check follow-up readings across the affected area."],
    "unknown": ["Collect more reliable meter and peer readings.",
                "Review the evidence with an operator before taking action."],
}


def update_plan(
    session: Session, *, case_id: UUID, report_id: UUID, event_id: UUID,
    run_id: UUID, evidence_ids: list[UUID],
) -> models.InvestigationPlan:
    """Derive scope from persisted evidence. Missing evidence cannot invalidate a plan."""
    case = session.scalar(select(models.Case).where(
        models.Case.id == case_id
    ).with_for_update())
    report = session.get(models.InvestigationReport, report_id)
    if case is None or report is None or report.case_id != case_id:
        raise ValueError("Plan must reference a report belonging to the case")
    run = session.get(models.AgentRun, run_id)
    if run is None or run.case_id != case_id or run.trigger.get("event_id") != str(event_id):
        raise ValueError("Plan must reference the case's triggering investigation run")
    evidence = [session.get(models.Evidence, item) for item in evidence_ids]
    if not evidence or any(item is None or item.case_id != case_id for item in evidence):
        raise ValueError("Plan evidence must belong to the case")
    by_kind = {item.kind: item for item in evidence}
    shared = by_kind.get("shared_incident")
    quality = by_kind.get("data_quality")
    scope = "unknown"
    if (shared and quality and quality.value_json.get("reliable") is True
            and shared.value_json.get("status") == "answered"):
        scope = shared.value_json.get("incident_type", "unknown")
    if scope not in GOALS:
        scope = "unknown"
    previous = session.scalar(select(models.InvestigationPlan).where(
        models.InvestigationPlan.case_id == case_id,
    ).order_by(models.InvestigationPlan.version.desc()))
    if previous and (previous.scope == scope or scope == "unknown"):
        return previous
    now = datetime.now(UTC)
    if previous:
        previous.status = "superseded" if previous.scope == "unknown" else "invalidated"
        previous.invalidated_at = now
        session.flush()
    plan = models.InvestigationPlan(
        case_id=case_id, version=previous.version + 1 if previous else 1,
        status="active", scope=scope, goal=GOALS[scope], steps_json=STEPS[scope],
        evidence_ids_json=[str(item) for item in evidence_ids],
        source_report_id=report_id, source_event_id=event_id, source_run_id=run_id,
        policy_version=POLICY_VERSION, created_at=now,
    )
    session.add(plan)
    session.flush()
    if previous:
        reason = (
            "Several related meters now show the same drop, suggesting a shared network issue."
            if scope == "shared" else
            "Reliable related-meter readings now support an individual meter investigation."
        )
        if previous.scope == "unknown":
            reason = "New reliable readings now identify the affected area."
        session.add(models.CaseEvent(
            case_id=case_id,
            event_type="plan_refined" if previous.scope == "unknown" else "plan_invalidated",
            created_at=now,
            details_json={
                "message": "New evidence changed the investigation plan.", "reason": reason,
                "old_plan_id": str(previous.id), "old_plan_version": previous.version,
                "new_plan_id": str(plan.id), "new_plan_version": plan.version,
                "previous_scope": previous.scope, "new_scope": scope,
                "contradicting_evidence_ids": (
                    [] if previous.scope == "unknown" else [str(shared.id)]
                ),
                "supporting_evidence_ids": [str(item) for item in evidence_ids],
                "event_id": str(event_id), "run_id": str(run_id),
                "report_id": str(report_id), "policy_version": POLICY_VERSION,
            },
        ))
    session.add(models.CaseEvent(
        case_id=case_id, event_type="plan_created", created_at=now,
        details_json={"plan_id": str(plan.id), "version": plan.version, "goal": plan.goal,
                      "message": "Investigation plan recorded."},
    ))
    session.flush()
    return plan
