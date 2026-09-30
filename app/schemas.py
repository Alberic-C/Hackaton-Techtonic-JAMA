"""Schémas Pydantic : payload n8n, état d'une décision, sortie structurée de Gemini, API."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from .models import ReviewOutcome, SessionStatus, SourceType


# =========================================================================== #
# 1. Webhook n8n  ->  POST /webhooks/n8n/documents
# =========================================================================== #
class N8nMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    author: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1, max_length=2000, description="Contexte métier / département / projet")
    date: datetime = Field(description="Date du document (ISO 8601)")
    source_url: HttpUrl | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def _tags_len(cls, v: list[str]) -> list[str]:
        if any(len(t) > 50 for t in v):
            raise ValueError("chaque tag doit faire <= 50 caractères")
        return v


class N8nDocumentPayload(BaseModel):
    """Contrat strict : tout champ inconnu est rejeté (422)."""

    model_config = ConfigDict(extra="forbid")

    external_id: str | None = Field(
        default=None, max_length=200, description="Identifiant stable côté n8n -> idempotence (upsert)"
    )
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=20, max_length=200_000, description="Texte intégral / retranscription")
    metadata: N8nMetadata
    reliability_score: int = Field(ge=0, le=100, description="100 = document interne, <50 = source web")
    source_type: Literal["internal", "web", "other"] | None = Field(
        default=None, description="Déduit du score si absent (>=80 internal, <50 web)"
    )

    def resolved_source_type(self) -> SourceType:
        if self.source_type:
            return SourceType(self.source_type)
        if self.reliability_score >= 80:
            return SourceType.INTERNAL
        if self.reliability_score < 50:
            return SourceType.WEB
        return SourceType.OTHER


class IngestResponse(BaseModel):
    document_id: int
    chunks: int
    status: Literal["created", "updated", "unchanged"]


# =========================================================================== #
# 2. État d'une décision (persisté dans ConversationSession.state)
# =========================================================================== #
class Solution(BaseModel):
    title: str = Field(max_length=200)
    description: str
    selected: bool = Field(default=False, description="Retenue par l'utilisateur pour la suite")
    source_refs: list[str] = Field(default_factory=list, description='Références RAG, ex: ["S1","S3"]')


class Scenario(BaseModel):
    """Garde-fou étape 2 : les 3 champs sont obligatoires pour avancer."""

    solution_title: str
    best_case: str = ""
    worst_case: str = ""
    if_it_fails: str = Field(default="", description="Plan B / que faire si ça échoue")


class Evaluation(BaseModel):
    solution_title: str
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    stress_test_summary: str = ""
    second_order_effects: str = ""
    reversibility: str = ""


class ActionItem(BaseModel):
    action: str
    owner: str = ""
    deadline: str = ""
    success_metric: str = ""


class SessionState(BaseModel):
    problem_statement: str | None = None
    root_cause: str | None = None
    root_cause_confirmed: bool = False
    solutions: list[Solution] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    evaluations: list[Evaluation] = Field(default_factory=list)
    chosen_solution: str | None = None
    action_plan: list[ActionItem] = Field(default_factory=list)
    decision_validated: bool = False
    decision_summary: str | None = None


# =========================================================================== #
# 3. Sortie structurée de Gemini (un tour de conversation)
#    null = inchangé ; une liste renvoyée REMPLACE la précédente.
# =========================================================================== #
class AgentTurn(BaseModel):
    reply: str
    problem_statement: str | None = None
    root_cause: str | None = None
    root_cause_confirmed: bool | None = None
    solutions: list[Solution] | None = None
    scenarios: list[Scenario] | None = None
    evaluations: list[Evaluation] | None = None
    chosen_solution: str | None = None
    action_plan: list[ActionItem] | None = None
    decision_validated: bool | None = None
    decision_summary: str | None = None


class ReviewLesson(BaseModel):
    """Traitement par Gemini du rapport d'évaluation (étape 5)."""

    headline: str = Field(description="Une phrase : ce qu'il faut retenir")
    outcome_vs_expectation: str
    decision_quality_assessment: str = Field(
        description="Qualité du processus de décision, indépendamment du résultat (éviter le 'resulting')"
    )
    what_worked: list[str]
    what_failed: list[str]
    key_lessons: list[str]
    advice_for_similar_cases: list[str]
    context_tags: list[str]


# =========================================================================== #
# 4. API de conversation
# =========================================================================== #
class SourceRef(BaseModel):
    ref: str
    chunk_id: int
    document_id: int
    title: str
    source_type: str
    reliability_score: int
    similarity: float
    excerpt: str


class Guardrails(BaseModel):
    can_advance: bool
    missing: list[str]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    step: int
    content: str
    sources: list[SourceRef] = Field(default_factory=list)
    created_at: datetime


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class UserMessage(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    current_step: int
    status: SessionStatus
    created_at: datetime
    updated_at: datetime


class SessionView(SessionSummary):
    step_name: str
    state: SessionState
    guardrails: Guardrails
    messages: list[MessageOut]


class TurnResult(BaseModel):
    reply: str
    step: int
    status: SessionStatus
    sources: list[SourceRef]
    state: SessionState
    guardrails: Guardrails


class SessionCreated(TurnResult):
    session_id: str


class ReviewCreate(BaseModel):
    session_id: str = Field(min_length=32, max_length=32)
    outcome: ReviewOutcome
    report: str = Field(min_length=20, max_length=10_000)


class ReviewOut(BaseModel):
    id: int
    session_id: str
    outcome: ReviewOutcome
    lessons: ReviewLesson
    document_id: int | None
    submitted_at: datetime
