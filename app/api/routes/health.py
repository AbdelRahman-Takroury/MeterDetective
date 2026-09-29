from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import models
from app.db.session import get_db
from app.scenario_two_fixture import METER_IDS as SCENARIO_TWO_METERS
from app.services.scenario_one import METER_IDS as SCENARIO_ONE_METERS

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["connected"]


class StatusComponent(BaseModel):
    status: Literal[
        "ready", "connected", "cached", "configured", "disabled", "missing",
        "unavailable", "not_checked", "running", "failed",
    ]
    detail: str
    critical: bool = False
    count: int | None = Field(default=None, ge=0)
    observed_at: datetime | None = None


class SystemStatusResponse(BaseModel):
    environment: Literal["simulation"]
    checked_at: datetime
    application: StatusComponent
    database: StatusComponent
    demonstration_dataset: StatusComponent
    knowledge_base: StatusComponent
    weather: StatusComponent
    narrative_service: StatusComponent
    latest_investigation: StatusComponent


@router.get("/health", response_model=HealthResponse)
def health(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection unavailable",
        ) from exc
    return HealthResponse(status="ok", database="connected")


def _unavailable(detail: str, *, critical: bool = False) -> StatusComponent:
    return StatusComponent(status="unavailable", detail=detail, critical=critical)


def _query_component(query: Any, db: Session, builder: Any) -> StatusComponent:
    try:
        return builder(db.execute(query))
    except SQLAlchemyError:
        return _unavailable("Status could not be read from the database.")


@router.get("/status", response_model=SystemStatusResponse)
def system_status(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SystemStatusResponse:
    """Return safe, read-only readiness metadata without probing external services."""
    now = datetime.now(UTC)
    database = StatusComponent(
        status="connected", detail="Investigation history can be stored.", critical=True
    )
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        database = _unavailable(
            "Investigation history cannot be stored. Restore the database connection.",
            critical=True,
        )

    scenario_ids = (*SCENARIO_ONE_METERS, *SCENARIO_TWO_METERS)
    if database.status == "connected":
        demonstration_dataset = _query_component(
            select(func.count()).select_from(models.Meter).where(models.Meter.id.in_(scenario_ids)),
            db,
            lambda result: StatusComponent(
                status=(
                    "ready"
                    if (count := int(result.scalar_one())) == len(scenario_ids)
                    else "missing"
                ),
                detail=(
                    "Both deterministic demonstration scenarios are available."
                    if count == len(scenario_ids)
                    else "Run the documented scenario preparation before demonstrating."
                ),
                critical=True,
                count=count,
            ),
        )
        knowledge_base = _query_component(
            select(func.count()).select_from(models.Document),
            db,
            lambda result: StatusComponent(
                status="ready" if (count := int(result.scalar_one())) > 0 else "missing",
                detail=(
                    "Stored technical documents are available for evidence search."
                    if count
                    else (
                        "No technical documents are stored; operational evidence remains available."
                    )
                ),
                count=count,
            ),
        )

        def weather_component(result: Any) -> StatusComponent:
            evidence = result.scalars().first()
            if evidence is None:
                return StatusComponent(
                    status="not_checked",
                    detail="Weather is checked only when an investigation needs it.",
                )
            value = evidence.value_json or {}
            if value.get("status") != "answered":
                return _unavailable(
                    "Weather was unavailable in the latest stored check; "
                    "investigations continue safely."
                )
            cached = bool(value.get("from_cache"))
            return StatusComponent(
                status="cached" if cached else "ready",
                detail=(
                    "The latest stored weather evidence used a safe cached response."
                    if cached
                    else "Weather evidence was available in the latest stored investigation."
                ),
                observed_at=evidence.created_at,
            )

        weather = _query_component(
            select(models.Evidence)
            .where(models.Evidence.kind == "weather_context")
            .order_by(models.Evidence.created_at.desc())
            .limit(1),
            db,
            weather_component,
        )

        def run_component(result: Any) -> StatusComponent:
            run = result.scalars().first()
            if run is None:
                return StatusComponent(
                    status="not_checked", detail="No stored investigation run is available yet."
                )
            run_status = run.status if run.status in {"running", "failed"} else "ready"
            return StatusComponent(
                status=run_status,
                detail=(
                    "The latest investigation completed and remains stored."
                    if run_status == "ready"
                    else "The latest investigation is still running."
                    if run_status == "running"
                    else (
                        "The latest investigation failed; review its stored activity "
                        "before retrying."
                    )
                ),
                observed_at=run.ended_at or run.started_at,
            )

        latest_investigation = _query_component(
            select(models.AgentRun).order_by(models.AgentRun.started_at.desc()).limit(1),
            db,
            run_component,
        )
    else:
        demonstration_dataset = _unavailable(
            "Dataset readiness cannot be checked while the database is unavailable.", critical=True
        )
        knowledge_base = _unavailable(
            "Technical-document readiness cannot be checked while the database is unavailable."
        )
        weather = _unavailable(
            "Stored weather evidence cannot be checked while the database is unavailable."
        )
        latest_investigation = _unavailable(
            "Investigation history cannot be checked while the database is unavailable."
        )

    if settings.llm_provider == "disabled":
        narrative = StatusComponent(
            status="disabled",
            detail=(
                "Optional narrative generation is disabled; deterministic investigation tools "
                "remain active."
            ),
        )
    elif settings.llm_api_key and settings.llm_model:
        narrative = StatusComponent(
            status="configured",
            detail=(
                "Optional narrative generation is configured but is not required for conclusions."
            ),
        )
    else:
        narrative = _unavailable(
            "Optional narrative generation is selected but its configuration is incomplete."
        )

    return SystemStatusResponse(
        environment="simulation",
        checked_at=now,
        application=StatusComponent(
            status="ready",
            detail="MeterDetective is responding in simulation mode.",
            critical=True,
        ),
        database=database,
        demonstration_dataset=demonstration_dataset,
        knowledge_base=knowledge_base,
        weather=weather,
        narrative_service=narrative,
        latest_investigation=latest_investigation,
    )
