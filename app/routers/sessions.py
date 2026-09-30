from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent import GuardrailViolation, SophiaAgent, StepLocked, guardrails_for
from ..config import Settings, get_settings
from ..database import get_db
from ..deps import get_llm, get_store
from ..gemini_client import LLMClient
from ..models import ConversationSession, SessionStatus
from ..prompts import STEP_NAMES
from ..schemas import (
    MessageOut,
    SessionCreate,
    SessionCreated,
    SessionState,
    SessionSummary,
    SessionView,
    TurnResult,
    UserMessage,
)
from ..vector_store import NumpyVectorStore

router = APIRouter(prefix="/api/sessions", tags=["conversation"])


def get_agent(
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
    store: NumpyVectorStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> SophiaAgent:
    return SophiaAgent(db, llm, store, settings)


def _load(db: Session, session_id: str) -> ConversationSession:
    s = db.get(ConversationSession, session_id)
    if s is None:
        raise HTTPException(404, "Session introuvable")
    return s


def _view(s: ConversationSession) -> SessionView:
    state = SessionState.model_validate(s.state)
    return SessionView(
        **SessionSummary.model_validate(s).model_dump(),
        step_name=STEP_NAMES[s.current_step],
        state=state,
        guardrails=guardrails_for(state, s.current_step),
        messages=[MessageOut.model_validate(m) for m in s.messages],
    )


@router.post("", response_model=SessionCreated, status_code=201)
def create_session(body: SessionCreate, agent: SophiaAgent = Depends(get_agent)):
    """Crée la session et renvoie le message d'ouverture de l'étape 1."""
    s, result = agent.create_session(body.title)
    return SessionCreated(session_id=s.id, **result.model_dump())


@router.get("", response_model=list[SessionSummary])
def list_sessions(
    status: SessionStatus | None = None, db: Session = Depends(get_db)
) -> list[ConversationSession]:
    q = select(ConversationSession).order_by(ConversationSession.updated_at.desc()).limit(100)
    if status:
        q = q.where(ConversationSession.status == status)
    return list(db.scalars(q))


@router.get("/{session_id}", response_model=SessionView)
def get_session(session_id: str, db: Session = Depends(get_db)):
    return _view(_load(db, session_id))


@router.post("/{session_id}/messages", response_model=TurnResult)
def post_message(
    session_id: str,
    body: UserMessage,
    db: Session = Depends(get_db),
    agent: SophiaAgent = Depends(get_agent),
):
    s = _load(db, session_id)
    try:
        return agent.handle_user_message(s, body.content)
    except StepLocked as e:
        raise HTTPException(409, str(e))


@router.post("/{session_id}/advance", response_model=TurnResult)
def advance(session_id: str, db: Session = Depends(get_db), agent: SophiaAgent = Depends(get_agent)):
    s = _load(db, session_id)
    try:
        return agent.advance(s)
    except StepLocked as e:
        raise HTTPException(409, str(e))
    except GuardrailViolation as e:
        raise HTTPException(422, {"message": "Garde-fou : étape incomplète", "missing": e.missing})
