"""Meter inventory and half-open reading-window endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app.api.schemas import MeterDetail, MeterListResponse, ReadingWindowResponse
from app.db.repositories import MeterRepository, ReadingRepository
from app.db.session import get_db

router = APIRouter(prefix="/meters", tags=["meters"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=MeterListResponse)
def list_meters(
    db: DbSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> MeterListResponse:
    items = MeterRepository(db).list(offset=offset, limit=limit)
    return MeterListResponse(items=items, offset=offset, limit=limit)


@router.get("/{meter_id}", response_model=MeterDetail)
def get_meter(meter_id: str, db: DbSession) -> MeterDetail:
    meter = MeterRepository(db).get(meter_id)
    if meter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")
    return MeterDetail.model_validate(meter)


@router.get("/{meter_id}/readings", response_model=ReadingWindowResponse)
def reading_window(
    meter_id: str,
    db: DbSession,
    start: Annotated[AwareDatetime, Query(description="Inclusive UTC-offset timestamp")],
    end: Annotated[AwareDatetime, Query(description="Exclusive UTC-offset timestamp")],
    limit: Annotated[int, Query(ge=1, le=10_000)] = 1000,
) -> ReadingWindowResponse:
    if MeterRepository(db).get(meter_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Meter not found")
    try:
        items = ReadingRepository(db).window(meter_id, start, end, limit=limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return ReadingWindowResponse(meter_id=meter_id, start=start, end=end, items=items, limit=limit)
