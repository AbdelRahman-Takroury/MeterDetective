"""Deterministic validation gate for persisted investigation reports."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.contracts.report import AnswerStatus, InvestigationReport

REQUIRED_ANSWER_FIELDS = {
    "question_id",
    "status",
    "answer",
    "structured_values",
    "confidence",
    "supporting_evidence",
    "contradicting_evidence",
    "tools_used",
    "data_sources",
    "limitations",
    "fresh_as_of",
}


class ReportValidationError(ValueError):
    pass


def _contains_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, dict):
        return any(_contains_number(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_number(item) for item in value)
    return False


def validate_report_for_persistence(
    *, case_id: UUID, status: str, answers: list[dict[str, Any]]
) -> InvestigationReport:
    for index, answer in enumerate(answers, start=1):
        missing = REQUIRED_ANSWER_FIELDS - answer.keys()
        if missing:
            raise ReportValidationError(
                f"Question entry {index} is missing explicit fields: {sorted(missing)}"
            )
    try:
        report = InvestigationReport.model_validate(
            {
                "report_id": uuid4(),
                "case_id": case_id,
                "version": 1,
                "status": status,
                "answers": answers,
            }
        )
    except ValidationError as exc:
        raise ReportValidationError(str(exc)) from exc

    if status == "complete":
        pending = [
            item.question_id
            for item in report.answers
            if item.status is AnswerStatus.PENDING_VERIFICATION
        ]
        if pending:
            raise ReportValidationError(
                f"A complete report cannot contain pending questions: {pending}"
            )
    for answer in report.answers:
        if answer.status is AnswerStatus.ANSWERED and answer.fresh_as_of is None:
            raise ReportValidationError(
                f"Answered question {answer.question_id} requires an evidence freshness timestamp"
            )
        if _contains_number(answer.structured_values):
            deterministic_tools = [
                name for name in answer.tools_used if not name.lower().startswith("llm")
            ]
            if not deterministic_tools:
                raise ReportValidationError(
                    f"Question {answer.question_id} has numerical values supplied solely by an LLM"
                )
    return report


def is_contract_report_status(status: str) -> bool:
    return status in {
        "investigating",
        "awaiting_approval",
        "pending_verification",
        "complete",
        "incomplete",
    }
