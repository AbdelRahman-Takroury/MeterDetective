"""Initial case inventory endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.schemas import CaseListResponse
from app.db.repositories import CaseRepository
from app.db.session import get_db

router = APIRouter(prefix="/cases", tags=["cases"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=CaseListResponse)
def list_cases(
    db: DbSession,
    status: Annotated[str | None, Query(min_length=1, max_length=30)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CaseListResponse:
    items = CaseRepository(db).list(status=status, offset=offset, limit=limit)
    return CaseListResponse(items=items, offset=offset, limit=limit)
