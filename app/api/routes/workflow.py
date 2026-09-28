"""Approval-gated recommendation and simulated-action APIs."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    ApprovalDecisionRequest,
    RecommendationCreateRequest,
    RecommendationResponse,
    RecommendationWorkflowResponse,
    SimulationExecuteRequest,
    VerifyCaseRequest,
    VerifyCaseResponse,
)
from app.db.repositories import (
    ApprovalRequired,
    RepositoryNotFound,
    WorkflowConflict,
    WorkflowRepository,
)
from app.db.session import get_db
from app.services.case_workflow import CaseWorkflowFailure, CaseWorkflowService
from app.services.investigation import build_registry
from app.services.scenario_one import ScenarioOneService

router = APIRouter(tags=["workflow"])
DbSession = Annotated[Session, Depends(get_db)]


def _not_found(exc: RepositoryNotFound) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _conflict(exc: WorkflowConflict) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/cases/{case_id}/recommendations",
    response_model=RecommendationWorkflowResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_recommendation(
    case_id: UUID, request: RecommendationCreateRequest, db: DbSession
) -> RecommendationWorkflowResponse:
    try:
        output = CaseWorkflowService(db, build_registry()).propose(
            case_id=case_id,
            action_type=request.action_type,
            rationale=request.rationale,
            risk=request.risk,
            requires_approval=request.requires_approval,
        )
        repository = WorkflowRepository(db)
        recommendation = repository.get_recommendation(output.recommendation_id)
        action = repository.action_for_recommendation(output.recommendation_id)
        if recommendation is None or action is None:
            raise CaseWorkflowFailure("Proposal did not persist its workflow records")
        db.commit()
    except RepositoryNotFound as exc:
        db.rollback()
        raise _not_found(exc) from exc
    except (CaseWorkflowFailure, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return RecommendationWorkflowResponse(
        recommendation=recommendation, approval=None, action=action
    )


@router.post("/cases/{case_id}/verify", response_model=VerifyCaseResponse)
def verify_case(
    case_id: UUID, request: VerifyCaseRequest, db: DbSession
) -> VerifyCaseResponse:
    if request.window_start.tzinfo is None or request.window_end.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Verification timestamps must include a timezone",
        )
    try:
        output = CaseWorkflowService(db, build_registry()).verify(
            case_id=case_id,
            action_id=request.action_id,
            window_start=request.window_start,
            window_end=request.window_end,
        )
        db.commit()
    except CaseWorkflowFailure as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return VerifyCaseResponse.model_validate(output.model_dump())


@router.post(
    "/recommendations/{recommendation_id}/{decision}",
    response_model=RecommendationWorkflowResponse,
)
def decide_recommendation(
    recommendation_id: UUID,
    decision: Literal["approve", "reject"],
    request: ApprovalDecisionRequest,
    db: DbSession,
) -> RecommendationWorkflowResponse:
    repository = WorkflowRepository(db)
    persisted_decision = "approved" if decision == "approve" else "rejected"
    try:
        recommendation, approval, action = repository.decide(
            recommendation_id,
            decision=persisted_decision,
            decided_by=request.decided_by,
            comment=request.comment,
        )
        db.commit()
    except RepositoryNotFound as exc:
        db.rollback()
        raise _not_found(exc) from exc
    except WorkflowConflict as exc:
        db.rollback()
        raise _conflict(exc) from exc
    return RecommendationWorkflowResponse(
        recommendation=recommendation, approval=approval, action=action
    )


@router.post(
    "/actions/{action_id}/execute-simulation",
    response_model=RecommendationWorkflowResponse,
)
def execute_action_simulation(
    action_id: UUID, request: SimulationExecuteRequest, db: DbSession
) -> RecommendationWorkflowResponse:
    repository = WorkflowRepository(db)
    try:
        recommendation, approval, action = repository.execute_simulation(
            action_id, result=request.result
        )
        repair_result = ScenarioOneService(db).apply_repair(action)
        if repair_result is not None:
            action.result_json = {**request.result, **repair_result}
            db.flush()
        db.commit()
    except RepositoryNotFound as exc:
        db.rollback()
        raise _not_found(exc) from exc
    except ApprovalRequired as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except WorkflowConflict as exc:
        db.rollback()
        raise _conflict(exc) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return RecommendationWorkflowResponse(
        recommendation=recommendation, approval=approval, action=action
    )


@router.get(
    "/recommendations/{recommendation_id}", response_model=RecommendationResponse
)
def get_recommendation(
    recommendation_id: UUID, db: DbSession
) -> RecommendationResponse:
    recommendation = WorkflowRepository(db).get_recommendation(recommendation_id)
    if recommendation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found"
        )
    return RecommendationResponse.model_validate(recommendation)
