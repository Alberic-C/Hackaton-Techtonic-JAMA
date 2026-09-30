"""Étape 5 : rapport d'évaluation -> leçons (Gemini) -> vectorisation -> réinjection dans le RAG."""
import json

from sqlalchemy.orm import Session

from ..config import Settings
from ..gemini_client import ChatTurn, LLMClient
from ..models import ConversationSession, Review, ReviewOutcome, SessionStatus, SourceType
from ..prompts import REVIEW_SYSTEM
from ..schemas import ReviewLesson, SessionState
from .ingestion import ingest_document


class ReviewNotAllowed(Exception):
    pass


def _lesson_to_text(state: SessionState, outcome: ReviewOutcome, lesson: ReviewLesson) -> str:
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {i}" for i in items) or "- (rien)"

    return (
        f"# Retour d'expérience — {lesson.headline}\n\n"
        f"Problème : {state.problem_statement}\n"
        f"Cause racine : {state.root_cause}\n"
        f"Solution appliquée : {state.chosen_solution}\n"
        f"Résultat : {outcome.value} — {lesson.outcome_vs_expectation}\n\n"
        f"Qualité de la décision : {lesson.decision_quality_assessment}\n\n"
        f"Ce qui a fonctionné :\n{bullets(lesson.what_worked)}\n\n"
        f"Ce qui n'a pas fonctionné :\n{bullets(lesson.what_failed)}\n\n"
        f"Leçons clés :\n{bullets(lesson.key_lessons)}\n\n"
        f"Conseils pour des cas similaires :\n{bullets(lesson.advice_for_similar_cases)}"
    )


def submit_review(
    db: Session,
    llm: LLMClient,
    settings: Settings,
    session: ConversationSession,
    outcome: ReviewOutcome,
    report: str,
) -> Review:
    if session.status != SessionStatus.AWAITING_REVIEW:
        raise ReviewNotAllowed("La décision n'est pas en attente d'évaluation.")

    state = SessionState.model_validate(session.state)
    payload = json.dumps(
        {
            "decision": state.model_dump(mode="json"),
            "resultat_declare": outcome.value,
            "rapport_utilisateur": report,
        },
        ensure_ascii=False,
    )
    lesson = llm.generate_structured(REVIEW_SYSTEM, [ChatTurn(role="user", text=payload)], ReviewLesson)

    result = ingest_document(
        db,
        llm,
        settings,
        external_id=f"review:{session.id}",
        title=f"Retour d'expérience : {state.chosen_solution or session.title}",
        text=_lesson_to_text(state, outcome, lesson),
        source_type=SourceType.FEEDBACK,
        reliability_score=settings.feedback_reliability,
        author="Sophia — boucle d'apprentissage",
        context=state.problem_statement,
        tags=["feedback", outcome.value, *lesson.context_tags[:10]],
        commit=False,
    )
    review = Review(
        session_id=session.id,
        outcome=outcome,
        report=report,
        lessons=lesson.model_dump(),
        document_id=result.document_id,
    )
    session.status = SessionStatus.REVIEWED
    db.add(review)
    db.commit()  # review + document + chunks + statut : une seule transaction
    return review
