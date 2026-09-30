"""Étape 5 — endpoint distinct, appelé des jours/semaines après la fin de la conversation."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..deps import get_llm
from ..gemini_client import LLMClient
from ..models import ConversationSession, SessionStatus
from ..schemas import ReviewCreate, ReviewLesson, ReviewOut, SessionSummary
from ..services.reviews import ReviewNotAllowed, submit_review

router = APIRouter(prefix="/api/reviews", tags=["learning"])


@router.get("/pending", response_model=list[SessionSummary])
def pending_reviews(db: Session = Depends(get_db)):
    """Décisions validées qui attendent leur rapport d'évaluation."""
    return list(
        db.scalars(
            select(ConversationSession)
            .where(ConversationSession.status == SessionStatus.AWAITING_REVIEW)
            .order_by(ConversationSession.updated_at)
        )
    )


@router.post("", response_model=ReviewOut, status_code=201)
def create_review(
    body: ReviewCreate,
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
    settings: Settings = Depends(get_settings),
):
    session = db.get(ConversationSession, body.session_id)
    if session is None:
        raise HTTPException(404, "Session introuvable")
    try:
        review = submit_review(db, llm, settings, session, body.outcome, body.report)
    except ReviewNotAllowed as e:
        raise HTTPException(409, str(e))
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Une évaluation existe déjà pour cette décision")
    return ReviewOut(
        id=review.id,
        session_id=review.session_id,
        outcome=review.outcome,
        lessons=ReviewLesson.model_validate(review.lessons),
        document_id=review.document_id,
        submitted_at=review.submitted_at,
    )
