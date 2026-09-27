from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_investigation_workflow import seed_scenario

from app.db import models
from app.db.base import Base
from app.services.investigation import InvestigationService, ReplayCommand, build_registry
from app.tools.weather import WeatherClient


def _run() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            event_time = seed_scenario(session)

            def unavailable_weather(url: str, timeout: float):
                raise TimeoutError

            service = InvestigationService(
                session,
                build_registry(
                    weather_client=WeatherClient(
                        base_url="https://weather.invalid",
                        retry_count=1,
                        transport=unavailable_weather,
                    )
                ),
            )
            result = service.replay(ReplayCommand(meter_id="M1", event_time=event_time))
            session.flush()
            traces = {
                row.tool_name: row.output_json
                for row in session.scalars(
                    select(models.ToolExecution).where(models.ToolExecution.run_id == result.run_id)
                )
            }
            report = session.get(models.InvestigationReport, result.report_id)
            assert report is not None
            return traces, report.answers_json
    finally:
        engine.dispose()


def _projection(traces: dict[str, Any], answers: list[dict[str, Any]]) -> dict[str, Any]:
    profile = next(
        item
        for item in traces["calculate_baseline"]["profiles"]
        if (item["weekday"], item["hour"], item["minute"]) == (6, 12, 0)
    )
    anomaly = traces["detect_anomaly"]
    event = next(e for e in anomaly["events"] if e["timestamp"] == "2026-09-20T12:00:00Z")
    hybrid = traces["calculate_anomaly_severity"]["values"]
    energy = traces["calculate_energy_balance"]
    return {
        "quality": traces["validate_reading_quality"],
        "baseline": {
            k: profile[k] for k in ("sample_count", "median_kwh", "q1_kwh", "q3_kwh", "iqr_kwh")
        },
        "anomaly": {
            "status": anomaly["status"],
            "overall_severity": anomaly["overall_severity"],
            "timestamp": event["timestamp"],
            "type": event["anomaly_type"],
            "observed": event["observed_kwh"],
            "expected": event["expected_kwh"],
            "severity": event["severity"],
            "components": event["components"],
        },
        "forecast": {
            k: traces["forecast_expected_usage"][k]
            for k in ("status", "expected_kwh", "method", "support_count")
        },
        "peer_selection": [
            {k: p[k] for k in ("meter_id", "similarity", "overlap")}
            for p in traces["select_dynamic_peers"]["peers"]
        ],
        "peer": {
            k: traces["compare_with_peers"][k]
            for k in (
                "status",
                "target_kwh",
                "peer_count",
                "peer_median_kwh",
                "deviation_pct",
                "percentile",
            )
        },
        "shared": {
            k: traces["detect_shared_incident"][k]
            for k in (
                "status",
                "incident_type",
                "confidence",
                "affected_count",
                "answered_count",
                "affected_fraction",
            )
        },
        "isolation": {
            k: traces["detect_isolation_forest_anomaly"]["values"][k]
            for k in (
                "status",
                "is_anomaly",
                "isolation_label",
                "isolation_score",
                "training_samples",
                "contamination",
                "random_state",
                "method_version",
            )
        },
        "hybrid": {
            "status": traces["calculate_anomaly_severity"]["status"],
            "score": hybrid["severity_score"],
            "components": hybrid["normalized_components"],
            "raw_day3": hybrid["raw_day3_severity"],
        },
        "energy": {
            "status": energy["status"],
            "balance_status": energy["balance_status"],
            "values": energy["values"],
        },
        "topology": {
            "status": traces["get_connected_assets"]["status"],
            "connected_meter_ids": traces["get_connected_assets"]["connected_meter_ids"],
        },
        "weather": traces["get_weather_context"]["status"],
        "der": {
            k: traces["analyze_customer_der_context"][k]
            for k in ("status", "customer_behavior", "ev", "solar", "persistence", "method_version")
        },
        "revenue": {
            k: traces["estimate_revenue_at_risk"][k]
            for k in ("status", "confidence", "values", "tariff_id", "reason")
        },
        "precedents": {
            k: traces["find_meter_precedents"][k]
            for k in ("status", "exact_meter_cases", "similar_system_cases", "pattern_summary")
        },
        "triage": {
            k: traces["calculate_triage_priority"][k]
            for k in ("status", "score", "band", "active_rank", "active_count", "percentile")
        },
        "q_statuses": [a["status"] for a in answers],
    }


def test_base_scenario1_golden_projection() -> None:
    traces, answers = _run()
    p = _projection(traces, answers)
    assert p["quality"]["status"] == "valid"
    assert p["quality"]["quality_score"] == 100.0
    assert p["quality"]["reliable"] is True
    assert p["quality"]["metrics"] == {
        "total_readings": 1345,
        "missing_values": 0,
        "negative_values": 0,
        "zero_values": 0,
        "duplicate_timestamps": 0,
        "missing_intervals": 0,
        "max_flatline_run": 1,
    }
    assert p["baseline"] == {
        "sample_count": 4,
        "median_kwh": 9.7,
        "q1_kwh": 9.7,
        "q3_kwh": 9.7,
        "iqr_kwh": 0.0,
    }
    assert p["anomaly"]["status"] == "anomalies_detected"
    assert p["anomaly"]["type"] == "drop"
    assert p["anomaly"]["observed"] == 3.0 and p["anomaly"]["expected"] == 9.7
    assert p["anomaly"]["severity"] == pytest.approx(69.07)
    assert p["anomaly"]["overall_severity"] == pytest.approx(69.07)
    assert p["anomaly"]["components"]["deviation_pct"] == pytest.approx(69.07)
    assert p["anomaly"]["components"]["lower_bound"] == pytest.approx(8.73)
    assert p["forecast"] == {
        "status": "answered",
        "expected_kwh": 9.7,
        "method": "seasonal_weekday_halfhour",
        "support_count": 4,
    }
    assert p["peer_selection"] == [
        {"meter_id": "M2", "similarity": 1.0, "overlap": 1344},
        {"meter_id": "M3", "similarity": 1.0, "overlap": 1344},
    ]
    assert p["peer"] == {
        "status": "answered",
        "target_kwh": 3.0,
        "peer_count": 2,
        "peer_median_kwh": 10.6,
        "deviation_pct": -71.7,
        "percentile": 0.0,
    }
    assert p["shared"] == {
        "status": "answered",
        "incident_type": "local",
        "confidence": 1.0,
        "affected_count": 0,
        "answered_count": 2,
        "affected_fraction": 0.0,
    }
    assert p["isolation"] == {
        "status": "success",
        "is_anomaly": False,
        "isolation_label": "normal",
        "isolation_score": 0.0,
        "training_samples": 1338,
        "contamination": 0.05,
        "random_state": 42,
        "method_version": "isolation_forest_v3",
    }
    assert p["hybrid"]["status"] == "answered" and p["hybrid"]["score"] == pytest.approx(38.51)
    assert p["hybrid"]["components"] == {
        "usage_deviation": 0.6907,
        "rule_based": 0.0,
        "isolation_forest": 0.0,
        "peer_deviation": 0.717,
    }
    assert p["hybrid"]["raw_day3"] == pytest.approx(69.07)
    assert p["energy"]["status"] == "answered" and p["energy"]["balance_status"] == "balanced"
    for key, value in (
        ("downstream_kwh", 24.2),
        ("expected_transformer_kwh", 24.926),
        ("actual_transformer_kwh", 24.926),
    ):
        assert p["energy"]["values"][key] == pytest.approx(value)
    assert p["energy"]["values"]["tolerance_pct"] == 0.05
    assert p["energy"]["values"]["technical_loss_rate"] == 0.03
    assert p["energy"]["values"]["method_version"] == "energy_balance_v2"
    assert p["topology"] == {"status": "answered", "connected_meter_ids": ["M2", "M3"]}
    assert p["weather"] == "unavailable"
    assert p["der"]["status"] == "answered"
    assert p["der"]["customer_behavior"]["evidence"] == "not_supporting"
    assert p["der"]["ev"]["has_ev"] is False and p["der"]["ev"]["evidence"] == "not_supporting"
    assert (
        p["der"]["solar"]["has_solar"] is False
        and p["der"]["solar"]["evidence"] == "not_supporting"
    )
    assert p["der"]["persistence"]["is_persistent"] is False
    assert p["der"]["persistence"]["direction"] == "decrease"
    assert p["der"]["persistence"]["consecutive_prior_intervals"] == 0
    assert p["der"]["persistence"]["previous_readings_available"] == 4
    assert p["der"]["method_version"] == "customer_der_context_v3"
    assert p["revenue"] == {
        "status": "unknown",
        "confidence": 0.0,
        "values": {},
        "tariff_id": None,
        "reason": "Tariff is unavailable",
    }
    assert p["precedents"] == {
        "status": "unknown",
        "exact_meter_cases": [],
        "similar_system_cases": [],
        "pattern_summary": "No historical precedents were found.",
    }
    assert p["triage"]["status"] == "answered" and p["triage"]["score"] == pytest.approx(19.63)
    assert p["triage"]["band"] == "P4" and p["triage"]["active_rank"] == 1
    assert p["triage"]["active_count"] == 1 and p["triage"]["percentile"] == 100.0

    # Known semantic mismatch: precedent tool is unknown, while report Q17 says answered.
    assert traces["find_meter_precedents"]["status"] == "unknown"
    assert answers[16]["status"] == "answered"
    assert p["q_statuses"] == [
        "answered",
        "answered",
        "answered",
        "answered",
        "answered",
        "unknown",
        "answered",
        "answered",
        "answered",
        "answered",
        "answered",
        "answered",
        "unknown",
        "unknown",
        "pending_verification",
        "unknown",
        "answered",
        "answered",
    ]


def test_base_scenario1_stable_projection_repeats_across_fresh_runs() -> None:
    traces_a, answers_a = _run()
    traces_b, answers_b = _run()
    assert _projection(traces_a, answers_a) == _projection(traces_b, answers_b)
