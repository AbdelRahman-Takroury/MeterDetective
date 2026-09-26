from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.base import Base
from app.services.llm import InvestigationNarrative, LLMProviderError, StructuredLLMAdapter
from app.services.report_validation import ReportValidationError, validate_report_for_persistence
from app.tools.knowledge import (
    KnowledgeDocument,
    KnowledgeIngestor,
    KnowledgeSearchInput,
    KnowledgeSearchTool,
)
from app.tools.weather import WeatherClient, WeatherContextInput


def test_weather_retries_once_returns_controlled_failure_and_caches_success() -> None:
    calls = 0

    def failing_transport(url: str, timeout: float):
        nonlocal calls
        calls += 1
        raise TimeoutError

    now = datetime(2026, 9, 20, tzinfo=UTC)
    request = WeatherContextInput(
        run_id=uuid4(), latitude=31.95, longitude=35.91, start=now, end=now + timedelta(hours=1)
    )
    failed = WeatherClient(
        base_url="https://weather.invalid",
        retry_count=1,
        transport=failing_transport,
    ).get(request)
    assert calls == 2
    assert failed.status == "unavailable"
    assert failed.confidence == 0
    assert failed.warnings

    success_calls = 0

    def successful_transport(url: str, timeout: float):
        nonlocal success_calls
        success_calls += 1
        return {
            "hourly": {
                "time": ["2026-09-20T00:00", "2026-09-20T01:00"],
                "temperature_2m": [20, 22],
                "precipitation": [0, 0.2],
            }
        }

    client = WeatherClient(base_url="https://weather.example", transport=successful_transport)
    first = client.get(request)
    second = client.get(request)
    assert first.status == "answered"
    assert first.temperature_c_mean == 21
    assert second.from_cache is True
    assert success_calls == 1


def test_knowledge_ingestion_citations_and_no_result() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = KnowledgeDocument(
            title="Trusted meter guidance",
            source_url="https://example.org/meter-guidance",
            license="CC0-1.0",
            content="Transformer mismatch and peer comparison support smart meter investigations.",
            metadata={"publisher": "Example standards body"},
        )
        assert KnowledgeIngestor().ingest(session, [source]) == (1, 1)
        assert KnowledgeIngestor().ingest(session, [source]) == (1, 1)
        assert session.scalar(select(func.count()).select_from(models.Document)) == 1
        assert session.scalar(select(func.count()).select_from(models.DocumentChunk)) == 1

        result = KnowledgeSearchTool()(
            KnowledgeSearchInput(run_id=uuid4(), query="transformer mismatch"), session
        )
        assert result.status == "answered"
        citation = result.citations[0]
        assert citation.source_url == source.source_url
        assert citation.license == source.license
        assert citation.relevance > 0
        assert result.evidence[0].metadata["title"] == source.title

        empty = KnowledgeSearchTool()(
            KnowledgeSearchInput(
                run_id=uuid4(), query="unrelated vocabulary", minimum_relevance=0.99
            ),
            session,
        )
        assert empty.status == "no_results"
        assert empty.warnings


def test_llm_invalid_json_gets_exactly_one_repair_and_numerical_extras_are_rejected() -> None:
    responses = iter(
        [
            {"choices": [{"message": {"content": "not-json"}}]},
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"summary":"x","hypothesis_interpretation":"y",'
                            '"limitations":[],"requested_tools":[],"severity":99}'
                        }
                    }
                ]
            },
        ]
    )
    calls = 0

    def transport(url: str, payload: dict, headers: dict, timeout: float):
        nonlocal calls
        calls += 1
        return next(responses)

    adapter = StructuredLLMAdapter(
        provider="openai_compatible",
        base_url="https://llm.example/v1",
        api_key="test",
        model="test-model",
        timeout_seconds=1,
        transport=transport,
    )
    with pytest.raises(LLMProviderError, match="one repair retry"):
        adapter.generate("Summarize stored evidence", InvestigationNarrative)
    assert calls == 2


def _answers() -> list[dict]:
    observed = datetime(2026, 9, 20, tzinfo=UTC).isoformat()
    result = []
    for question_id in range(1, 19):
        result.append(
            {
                "question_id": question_id,
                "status": "answered",
                "answer": "Evidence-backed answer.",
                "structured_values": {},
                "confidence": 0.8,
                "supporting_evidence": [
                    {
                        "source": "deterministic_tool",
                        "kind": "calculation",
                        "reliability": 1,
                        "observed_at": observed,
                        "metadata": {},
                    }
                ],
                "contradicting_evidence": [],
                "tools_used": ["deterministic_tool"],
                "data_sources": ["fixture"],
                "limitations": [],
                "fresh_as_of": observed,
            }
        )
    return result


def test_complete_report_gate_rejects_missing_fields_and_llm_only_numbers() -> None:
    missing = _answers()
    del missing[0]["limitations"]
    with pytest.raises(ReportValidationError, match="explicit fields"):
        validate_report_for_persistence(case_id=uuid4(), status="complete", answers=missing)

    numeric = _answers()
    numeric[1]["structured_values"] = {"severity": 92}
    numeric[1]["tools_used"] = ["llm_narrative"]
    with pytest.raises(ReportValidationError, match="solely by an LLM"):
        validate_report_for_persistence(case_id=uuid4(), status="complete", answers=numeric)

    assert (
        validate_report_for_persistence(
            case_id=uuid4(), status="complete", answers=_answers()
        ).completeness
        == 1
    )
