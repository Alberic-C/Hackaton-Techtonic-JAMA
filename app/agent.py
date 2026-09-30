"""Agent Sophia : machine à états en 5 étapes, persistée via SQLAlchemy.

Le LLM propose (mises à jour d'état structurées) ; le code dispose :
  - chaque étape n'autorise que certains champs (`ALLOWED_FIELDS`) ;
  - les confirmations utilisateur ne sont acceptées qu'un tour APRÈS la proposition ;
  - le passage à l'étape suivante n'est possible que si `missing_for_advance()` est vide.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .gemini_client import ChatTurn, LLMClient
from .models import ConversationSession, Message, SessionStatus
from .prompts import OPENING_TRIGGER, STEP_NAMES, build_system_prompt
from .schemas import AgentTurn, Guardrails, SessionState, TurnResult
from .vector_store import NumpyVectorStore

MAX_STEP = 5

ALLOWED_FIELDS: dict[int, set[str]] = {
    1: {"problem_statement", "root_cause", "root_cause_confirmed"},
    2: {"solutions", "scenarios"},
    3: {"evaluations", "chosen_solution"},
    4: {"action_plan", "decision_validated"},
    5: {"decision_summary"},
}


class StepLocked(Exception):
    """Conversation impossible dans l'état courant (ex. étape 5 en attente de review)."""


class GuardrailViolation(Exception):
    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("; ".join(missing))


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


# --------------------------------------------------------------------------- #
# Garde-fous (fonctions pures -> testables sans LLM)
# --------------------------------------------------------------------------- #
def missing_for_advance(state: SessionState, step: int) -> list[str]:
    missing: list[str] = []
    selected = [s for s in state.solutions if s.selected]
    if step == 1:
        if not state.problem_statement:
            missing.append("Le problème n'est pas encore formulé.")
        if not state.root_cause:
            missing.append("La cause racine n'est pas encore identifiée.")
        elif not state.root_cause_confirmed:
            missing.append("Tu dois confirmer explicitement que c'est la racine et non un symptôme.")
    elif step == 2:
        if not selected:
            missing.append("Retiens au moins une solution.")
        by_title = {_norm(sc.solution_title): sc for sc in state.scenarios}
        for sol in selected:
            sc = by_title.get(_norm(sol.title))
            gaps = (
                ["meilleur scénario", "pire scénario", "plan si ça échoue"]
                if sc is None
                else [
                    label
                    for label, val in (
                        ("meilleur scénario", sc.best_case),
                        ("pire scénario", sc.worst_case),
                        ("plan si ça échoue", sc.if_it_fails),
                    )
                    if not val.strip()
                ]
            )
            if gaps:
                missing.append(f"« {sol.title} » : il manque {', '.join(gaps)}.")
    elif step == 3:
        evals = {_norm(e.solution_title): e for e in state.evaluations}
        for sol in selected:
            e = evals.get(_norm(sol.title))
            if e is None or not e.pros or not e.cons:
                missing.append(f"« {sol.title} » : avantages/inconvénients à compléter.")
        if not state.chosen_solution:
            missing.append("Choisis l'option à mettre en œuvre.")
        elif _norm(state.chosen_solution) not in {_norm(s.title) for s in selected}:
            missing.append("L'option choisie doit faire partie des solutions retenues.")
    elif step == 4:
        if not state.action_plan:
            missing.append("Le plan d'action est vide.")
        if not state.decision_validated:
            missing.append("Tu dois valider explicitement la décision et le plan.")
    else:
        missing.append("Parcours terminé : soumets ton rapport d'évaluation plus tard.")
    return missing


def guardrails_for(state: SessionState, step: int) -> Guardrails:
    m = missing_for_advance(state, step)
    return Guardrails(can_advance=not m and step < MAX_STEP, missing=m)


def apply_turn(state: SessionState, turn: AgentTurn, step: int) -> SessionState:
    """Fusionne la sortie du LLM dans l'état, en appliquant les règles d'étape."""
    new = state.model_copy(deep=True)
    allowed = ALLOWED_FIELDS[step]

    if "problem_statement" in allowed and turn.problem_statement:
        new.problem_statement = turn.problem_statement.strip()

    if "root_cause" in allowed:
        if turn.root_cause and _norm(turn.root_cause) != _norm(state.root_cause or ""):
            new.root_cause = turn.root_cause.strip()
            new.root_cause_confirmed = False  # cause modifiée => à reconfirmer
        elif turn.root_cause_confirmed is not None and state.root_cause:
            # Une cause doit exister AVANT ce tour : impossible de proposer et confirmer d'un coup.
            new.root_cause_confirmed = turn.root_cause_confirmed

    if "solutions" in allowed and turn.solutions is not None:
        new.solutions = turn.solutions
    if "scenarios" in allowed and turn.scenarios is not None:
        new.scenarios = turn.scenarios
    if "evaluations" in allowed and turn.evaluations is not None:
        new.evaluations = turn.evaluations
    if "chosen_solution" in allowed and turn.chosen_solution:
        new.chosen_solution = turn.chosen_solution.strip()
    if "action_plan" in allowed and turn.action_plan is not None:
        if turn.action_plan != state.action_plan:
            new.decision_validated = False  # plan modifié => à revalider
        new.action_plan = turn.action_plan
    if "decision_validated" in allowed and turn.decision_validated is not None and state.action_plan:
        if turn.action_plan is None or turn.action_plan == state.action_plan:
            new.decision_validated = turn.decision_validated
    if "decision_summary" in allowed and turn.decision_summary:
        new.decision_summary = turn.decision_summary
    return new


# --------------------------------------------------------------------------- #
class SophiaAgent:
    def __init__(self, db: Session, llm: LLMClient, store: NumpyVectorStore, settings: Settings):
        self.db, self.llm, self.store, self.s = db, llm, store, settings

    # -- RAG (étape 2) ---------------------------------------------------------
    def _retrieve(self, state: SessionState, user_text: str | None) -> list[dict]:
        query = "\n".join(
            x for x in (state.problem_statement, state.root_cause, user_text) if x
        ).strip()
        if not query:
            return []
        qvec = self.llm.embed([query], "RETRIEVAL_QUERY")[0]
        hits = self.store.search(
            self.db,
            qvec,
            top_k=self.s.rag_top_k,
            min_reliability=self.s.rag_min_reliability,
            min_similarity=0.2,
        )
        return [
            {
                "ref": f"S{i}",
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "title": h.title,
                "source_type": h.source_type,
                "reliability_score": h.reliability_score,
                "similarity": round(h.similarity, 4),
                "excerpt": h.text[:280],
                "full_text": h.text,
            }
            for i, h in enumerate(hits, start=1)
        ]

    def _history(self, session_id: str, step: int) -> list[ChatTurn]:
        rows = self.db.scalars(
            select(Message)
            .where(Message.session_id == session_id, Message.step == step)
            .order_by(Message.id.desc())
            .limit(self.s.max_history_messages)
        ).all()
        return [
            ChatTurn(role="user" if m.role == "user" else "model", text=m.content) for m in reversed(rows)
        ]

    # -- un tour de conversation ----------------------------------------------
    def _converse(
        self, s: ConversationSession, step: int, state: SessionState, user_text: str | None
    ) -> tuple[AgentTurn, SessionState, list[dict]]:
        sources = self._retrieve(state, user_text) if step == 2 else []
        system = build_system_prompt(step, state, sources)
        turns = self._history(s.id, step) + [ChatTurn(role="user", text=user_text or OPENING_TRIGGER)]
        turn = self.llm.generate_structured(system, turns, AgentTurn)
        return turn, apply_turn(state, turn, step), sources

    def _persist_turn(
        self,
        s: ConversationSession,
        step: int,
        user_text: str | None,
        turn: AgentTurn,
        new_state: SessionState,
        sources: list[dict],
    ) -> TurnResult:
        if user_text is not None:
            s.messages.append(Message(step=step, role="user", content=user_text))
        public_sources = [{k: v for k, v in src.items() if k != "full_text"} for src in sources]
        s.messages.append(Message(step=step, role="assistant", content=turn.reply, sources=public_sources))
        s.current_step = step
        s.state = new_state.model_dump(mode="json")
        self.db.add(s)
        self.db.commit()
        return TurnResult(
            reply=turn.reply,
            step=step,
            status=s.status,
            sources=public_sources,
            state=new_state,
            guardrails=guardrails_for(new_state, step),
        )

    # -- API de l'agent ---------------------------------------------------------
    def create_session(self, title: str | None) -> tuple[ConversationSession, TurnResult]:
        s = ConversationSession(
            id=uuid.uuid4().hex,
            title=(title or "Nouvelle décision").strip(),
            current_step=1,
            status=SessionStatus.ACTIVE,
            state=SessionState().model_dump(mode="json"),
        )
        state = SessionState()
        turn, new_state, sources = self._converse(s, 1, state, None)
        return s, self._persist_turn(s, 1, None, turn, new_state, sources)

    def handle_user_message(self, s: ConversationSession, content: str) -> TurnResult:
        if s.status != SessionStatus.ACTIVE or s.current_step >= MAX_STEP:
            raise StepLocked(
                "Cette décision est validée. Soumets ton rapport d'évaluation via POST /api/reviews."
            )
        state = SessionState.model_validate(s.state)
        turn, new_state, sources = self._converse(s, s.current_step, state, content)
        return self._persist_turn(s, s.current_step, content, turn, new_state, sources)

    def advance(self, s: ConversationSession) -> TurnResult:
        if s.status != SessionStatus.ACTIVE or s.current_step >= MAX_STEP:
            raise StepLocked("Plus d'étape suivante.")
        state = SessionState.model_validate(s.state)
        missing = missing_for_advance(state, s.current_step)
        if missing:
            raise GuardrailViolation(missing)
        nxt = s.current_step + 1
        # LLM d'abord : en cas d'échec, l'étape ne change pas.
        turn, new_state, sources = self._converse(s, nxt, state, None)
        if nxt == MAX_STEP:
            s.status = SessionStatus.AWAITING_REVIEW
        return self._persist_turn(s, nxt, None, turn, new_state, sources)

    @staticmethod
    def step_name(step: int) -> str:
        return STEP_NAMES[step]
