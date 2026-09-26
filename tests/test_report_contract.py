from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts.common import EvidenceReference
from app.contracts.report import (
    AnswerStatus,
    InvestigationAnswer,
    InvestigationReport,
    RevenueAtRisk,
    TriageAssessment,
)


def evidence() -> EvidenceReference:
    return EvidenceReference(source="test", kind="calculation", reliability=0.9)


def answer(question_id: int) -> InvestigationAnswer:
    return InvestigationAnswer(
        question_id=question_id,
        status=AnswerStatus.ANSWERED,
        answer=f"Answer {question_id}",
        confidence=0.8,
        supporting_evidence=[evidence()],
        tools_used=["test_tool"],
        data_sources=["test_fixture"],
        fresh_as_of=datetime.now(UTC),
    )


def report(**changes: object) -> InvestigationReport:
    data = {
        "report_id": uuid4(),
        "case_id": uuid4(),
        "version": 1,
        "status": "complete",
        "answers": [answer(index) for index in range(1, 19)],
        "revenue_at_risk": RevenueAtRisk(
            currency="JOD",
            low=1,
            base=2,
            high=3,
            tariff_version="synthetic-v1",
            assumptions=["test fixture"],
            confidence=0.7,
        ),
        "meter_pattern_summary": "No recurrence.",
        "triage": TriageAssessment(
            score=50,
            band="P2",
            active_rank=1,
            active_count=1,
            factors={"severity": 50},
            policy_version="v1",
        ),
    }
    data.update(changes)
    return InvestigationReport.model_validate(data)


def test_complete_report_has_all_answers() -> None:
    result = report()
    assert result.completeness == 1


def test_report_rejects_missing_question() -> None:
    with pytest.raises(ValidationError, match="each question ID"):
        report(answers=[answer(index) for index in range(1, 18)])


def test_answered_question_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence reference"):
        InvestigationAnswer(
            question_id=1,
            status=AnswerStatus.ANSWERED,
            answer="Unsupported",
            confidence=0.8,
        )
