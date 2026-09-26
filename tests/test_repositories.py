"""Behavioral checks for the transaction-neutral repository boundary."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.base import Base
from app.db.repositories import (
    CaseRepository,
    EventRepository,
    FinanceRepository,
    HistoryRepository,
    MeterRepository,
    RankingRepository,
    ReadingRepository,
    ReportRepository,
    RepositoryConflict,
    RepositoryNotFound,
    TariffRepository,
)

AT = datetime(2012, 5, 1, tzinfo=UTC)
TABLES = (
    models.Asset,
    models.Meter,
    models.Reading,
    models.TransformerReading,
    models.Event,
    models.Case,
    models.CaseMeter,
    models.CaseEvent,
    models.InvestigationReport,
    models.Tariff,
    models.FinancialImpact,
    models.TriageAssessment,
)


@pytest.fixture
def session():  # noqa: ANN201
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _record):  # noqa: ANN001
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine, tables=[model.__table__ for model in TABLES])
    with Session(engine) as db:
        yield db
    engine.dispose()


def _meter(session: Session) -> models.Asset:
    transformer = models.Asset(asset_type="transformer", name="TX_1")
    session.add(transformer)
    session.flush()
    session.add(
        models.Meter(id="M-1", transformer_id=transformer.id, type="residential", status="active")
    )
    session.flush()
    return transformer


def test_reading_and_event_repositories_are_idempotent(session: Session) -> None:
    transformer = _meter(session)
    readings = ReadingRepository(session)
    first = readings.add(meter_id="M-1", timestamp=AT, kwh=2.5, source="fixture")
    assert readings.add(meter_id="M-1", timestamp=AT, kwh=2.5, source="fixture").id == first.id
    with pytest.raises(RepositoryConflict):
        readings.add(meter_id="M-1", timestamp=AT, kwh=3, source="fixture")
    readings.add(meter_id="M-1", timestamp=AT + timedelta(minutes=30), kwh=3, source="fixture")
    assert [row.kwh for row in readings.window("M-1", AT, AT + timedelta(hours=1))] == [2.5, 3]
    assert len(readings.window("M-1", AT, AT + timedelta(minutes=30))) == 1
    with pytest.raises(ValueError, match="timezone"):
        readings.window("M-1", AT.replace(tzinfo=None), AT + timedelta(hours=1))
    with pytest.raises(ValueError, match="after start"):
        readings.window("M-1", AT, AT)

    session.add(models.TransformerReading(transformer_id=transformer.id, timestamp=AT, input_kwh=8))
    session.flush()
    assert (
        readings.transformer_window(transformer.id, AT, AT + timedelta(hours=1))[0].input_kwh == 8
    )

    events = EventRepository(session)
    event_row = events.add(
        idempotency_key="source:1",
        event_type="reading.received",
        payload={"meter": "M-1"},
        status="processed",
    )
    assert (
        events.add(
            idempotency_key="source:1",
            event_type="reading.received",
            payload={"meter": "M-1"},
            status="processed",
        ).id
        == event_row.id
    )
    event_row.status = "archived"
    assert (
        events.add(
            idempotency_key="source:1",
            event_type="reading.received",
            payload={"meter": "M-1"},
            status="processed",
        ).id
        == event_row.id
    )
    with pytest.raises(RepositoryConflict):
        events.add(
            idempotency_key="source:1",
            event_type="reading.received",
            payload={"meter": "M-2"},
            status="processed",
        )


def test_case_history_and_report_versions(session: Session) -> None:
    _meter(session)
    meters = MeterRepository(session)
    assert meters.get("M-1") is not None
    assert [meter.id for meter in meters.list()] == ["M-1"]

    cases = CaseRepository(session)
    case = cases.add(title="Unexpected drop", status="open", meter_ids=["M-1", "M-1"])
    assert cases.get(case.id) is case
    assert [item.id for item in cases.list(status="open")] == [case.id]
    assert cases.list(status="closed") == []
    assert [item.id for item in HistoryRepository(session).meter_cases("M-1")] == [case.id]
    assert HistoryRepository(session).meter_cases("M-1", closed_only=True) == []
    session.add(models.CaseEvent(case_id=case.id, event_type="created", details_json={}))
    session.flush()
    assert [item.event_type for item in HistoryRepository(session).case_events(case.id)] == [
        "created"
    ]

    reports = ReportRepository(session)
    first = reports.add_version(case_id=case.id, status="draft", answers=[], completeness=0)
    second = reports.add_version(
        case_id=case.id, status="partial", answers=[{"question_id": 1}], completeness=0.1
    )
    assert (first.version, second.version) == (1, 2)
    assert first.superseded_at is not None
    assert reports.latest(case.id).id == second.id
    assert [row.version for row in reports.versions(case.id)] == [1, 2]
    with pytest.raises(RepositoryNotFound):
        reports.add_version(case_id=uuid4(), status="draft", answers=[], completeness=0)


def test_tariff_finance_and_rankings(session: Session) -> None:
    _meter(session)
    cases = CaseRepository(session)
    case = cases.add(title="Case A", status="open", meter_ids=["M-1"])
    closed_case = cases.add(title="Case B", status="closed")
    closed_case.priority_band = "P1"
    session.flush()
    report = ReportRepository(session).add_version(
        case_id=case.id, status="partial", answers=[], completeness=0
    )

    tariff = models.Tariff(
        name="Residential bracket 1",
        customer_segment="residential",
        currency="JOD",
        jod_per_kwh=Decimal("0.050000"),
        effective_from=AT,
        source="fixture",
    )
    session.add(tariff)
    session.flush()
    assert [row.id for row in TariffRepository(session).effective("residential", AT)] == [tariff.id]
    assert TariffRepository(session).effective("residential", AT - timedelta(days=1)) == []

    finance = FinanceRepository(session)
    impact = finance.add(
        case_id=case.id,
        report_id=report.id,
        tariff_id=tariff.id,
        missing_kwh=(1, 2, 3),
        risk_jod=(Decimal("0.1"), Decimal("0.2"), Decimal("0.3")),
        assumptions=["synthetic"],
        confidence=0.8,
    )
    assert finance.by_report(report.id).id == impact.id
    with pytest.raises(RepositoryConflict):
        finance.add(
            case_id=case.id,
            report_id=report.id,
            tariff_id=tariff.id,
            missing_kwh=(1, 2, 3),
            risk_jod=(Decimal("0.1"), Decimal("0.2"), Decimal("0.3")),
            assumptions=[],
            confidence=0.8,
        )

    rankings = RankingRepository(session)
    assessment = rankings.add_assessment(
        case_id=case.id,
        report_id=report.id,
        score=90,
        band="P1",
        active_rank=1,
        active_count=1,
        percentile=100,
        factors={"severity": 90},
        policy_version="1",
    )
    assert rankings.by_report(report.id).id == assessment.id
    assert (case.triage_score, case.priority_band, case.active_rank) == (90, "P1", 1)
    assert [row.id for row in rankings.active_cases()] == [case.id]
    assert rankings.active_cases(statuses=()) == []
    with pytest.raises(RepositoryConflict):
        rankings.add_assessment(
            case_id=case.id,
            report_id=report.id,
            score=90,
            band="P1",
            active_rank=1,
            active_count=1,
            percentile=100,
            factors={},
            policy_version="1",
        )
    assert (
        session.scalar(select(models.Case).where(models.Case.id == closed_case.id)) is closed_case
    )


def test_repository_writes_remain_in_caller_transaction(session: Session) -> None:
    _meter(session)
    CaseRepository(session).add(title="Temporary", status="open", meter_ids=["M-1"])
    session.rollback()
    assert CaseRepository(session).list() == []
