"""Modèles ORM SQLAlchemy 2.0.

documents ──< document_chunks        (le RAG travaille au niveau chunk)
conversation_sessions ──< messages
conversation_sessions ──1 reviews ──? documents   (la leçon apprise devient un document)
"""
import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class SourceType(str, enum.Enum):
    INTERNAL = "internal"
    WEB = "web"
    FEEDBACK = "feedback"  # leçon issue de l'étape 5
    OTHER = "other"


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    AWAITING_REVIEW = "awaiting_review"  # étape 5 : l'utilisateur est parti appliquer sa décision
    REVIEWED = "reviewed"


class ReviewOutcome(str, enum.Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


# --------------------------------------------------------------------------- #
# Base documentaire
# --------------------------------------------------------------------------- #
class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("reliability_score BETWEEN 0 AND 100", name="ck_reliability_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(200), unique=True)  # idempotence n8n
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))

    # Métadonnées
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType, native_enum=False, length=20))
    author: Mapped[str | None] = mapped_column(String(200))
    context: Mapped[str | None] = mapped_column(Text)
    doc_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    reliability_score: Mapped[int] = mapped_column(Integer, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    """Unité de retrieval : texte + embedding float32 normalisé (BLOB)."""

    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_chunks_model", "embedding_model"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    embedding_model: Mapped[str] = mapped_column(String(100))
    # Dénormalisé depuis Document pour filtrer sans jointure dans le hot path.
    reliability_score: Mapped[int] = mapped_column(Integer)

    document: Mapped[Document] = relationship(back_populates="chunks")


# --------------------------------------------------------------------------- #
# Sessions de conversation
# --------------------------------------------------------------------------- #
class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 hex, non devinable
    title: Mapped[str] = mapped_column(String(300))
    current_step: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, native_enum=False, length=20), default=SessionStatus.ACTIVE, index=True
    )
    # SessionState (pydantic) sérialisé : problème, cause racine, solutions, scénarios, plan...
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="Message.id"
    )
    review: Mapped["Review | None"] = relationship(back_populates="session", uselist=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_step", "session_id", "step"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id", ondelete="CASCADE"))
    step: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)  # chunks RAG utilisés (assistant)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ConversationSession] = relationship(back_populates="messages")


# --------------------------------------------------------------------------- #
# Étape 5 (asynchrone)
# --------------------------------------------------------------------------- #
class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"), unique=True  # 1 review / décision
    )
    outcome: Mapped[ReviewOutcome] = mapped_column(SAEnum(ReviewOutcome, native_enum=False, length=20))
    report: Mapped[str] = mapped_column(Text)  # rapport brut de l'utilisateur
    lessons: Mapped[dict] = mapped_column(JSON, default=dict)  # sortie structurée de Gemini
    # Document créé (et vectorisé) à partir de la leçon -> réinjecté dans le RAG
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[ConversationSession] = relationship(back_populates="review")
    document: Mapped[Document | None] = relationship()
