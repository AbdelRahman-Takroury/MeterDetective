from sqlalchemy import CheckConstraint, Index, Numeric, UniqueConstraint

from app.db.base import Base


def _unique_constraint_columns(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _check_constraint_names(table_name: str) -> set[str | None]:
    table = Base.metadata.tables[table_name]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def _index_names(table_name: str) -> set[str | None]:
    table = Base.metadata.tables[table_name]
    return {index.name for index in table.indexes if isinstance(index, Index)}


def test_foundational_tables_are_registered() -> None:
    expected = {
        "assets",
        "meters",
        "readings",
        "transformer_readings",
        "events",
        "anomalies",
        "cases",
        "case_meters",
        "investigation_reports",
        "tariffs",
        "financial_impacts",
        "triage_assessments",
    }

    assert expected <= set(Base.metadata.tables)


def test_readings_and_events_have_idempotency_constraints() -> None:
    assert ("meter_id", "timestamp") in _unique_constraint_columns("readings")
    assert ("idempotency_key",) in _unique_constraint_columns("events")
    assert Base.metadata.tables["events"].c.idempotency_key.nullable is False


def test_report_outputs_are_unique_per_report() -> None:
    assert ("case_id", "version") in _unique_constraint_columns("investigation_reports")
    assert ("report_id",) in _unique_constraint_columns("financial_impacts")
    assert ("report_id",) in _unique_constraint_columns("triage_assessments")


def test_foundational_foreign_keys_preserve_domain_links() -> None:
    expected = {
        ("meters", "transformer_id"): "assets.id",
        ("readings", "meter_id"): "meters.id",
        ("transformer_readings", "transformer_id"): "assets.id",
        ("anomalies", "event_id"): "events.id",
        ("case_meters", "case_id"): "cases.id",
        ("case_meters", "meter_id"): "meters.id",
        ("investigation_reports", "case_id"): "cases.id",
        ("financial_impacts", "report_id"): "investigation_reports.id",
        ("triage_assessments", "report_id"): "investigation_reports.id",
    }

    for (table_name, column_name), target in expected.items():
        foreign_keys = Base.metadata.tables[table_name].c[column_name].foreign_keys
        assert {foreign_key.target_fullname for foreign_key in foreign_keys} == {target}


def test_jod_values_use_fixed_precision_types() -> None:
    tariffs = Base.metadata.tables["tariffs"]
    financial_impacts = Base.metadata.tables["financial_impacts"]

    assert isinstance(tariffs.c.jod_per_kwh.type, Numeric)
    assert isinstance(financial_impacts.c.risk_jod_base.type, Numeric)


def test_required_lookup_indexes_are_registered() -> None:
    assert "ix_readings_meter_timestamp" in _index_names("readings")
    assert "ix_transformer_readings_transformer_timestamp" in _index_names(
        "transformer_readings"
    )
    assert "ix_transformer_readings_timestamp" in _index_names("transformer_readings")
    assert "ix_cases_status_priority" in _index_names("cases")
    assert "ix_document_chunks_embedding_hnsw" in _index_names("document_chunks")


def test_numeric_domain_checks_are_registered() -> None:
    assert "ck_investigation_reports_report_completeness_range" in _check_constraint_names(
        "investigation_reports"
    )
    assert "ck_financial_impacts_financial_missing_kwh_order" in _check_constraint_names(
        "financial_impacts"
    )
    assert "ck_financial_impacts_financial_risk_jod_order" in _check_constraint_names(
        "financial_impacts"
    )
    assert "ck_triage_assessments_triage_rank_within_count" in _check_constraint_names(
        "triage_assessments"
    )
