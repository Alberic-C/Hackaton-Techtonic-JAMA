"""Ingestion : chunking -> embedding Google -> stockage SQLite. Idempotent via external_id."""
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..gemini_client import LLMClient
from ..models import Document, DocumentChunk, SourceType
from ..vector_store import to_blob


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Découpe par paragraphes, regroupés jusqu'à `size` ; un paragraphe trop long est fenêtré."""
    chunks: list[str] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text.strip()):
        para = para.strip()
        if not para:
            continue
        if len(para) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            step = max(size - overlap, 1)
            chunks.extend(para[i : i + size] for i in range(0, len(para), step))
            continue
        if buf and len(buf) + len(para) + 2 > size:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


@dataclass
class IngestResult:
    document_id: int
    chunks: int
    status: str  # created | updated | unchanged


def ingest_document(
    db: Session,
    llm: LLMClient,
    settings: Settings,
    *,
    external_id: str | None,
    title: str,
    text: str,
    source_type: SourceType,
    reliability_score: int,
    author: str | None = None,
    context: str | None = None,
    doc_date: datetime | None = None,
    source_url: str | None = None,
    tags: list[str] | None = None,
    commit: bool = True,
) -> IngestResult:
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    existing = (
        db.scalar(select(Document).where(Document.external_id == external_id)) if external_id else None
    )
    meta = dict(
        title=title,
        source_type=source_type,
        reliability_score=reliability_score,
        author=author,
        context=context,
        doc_date=doc_date,
        source_url=source_url,
        tags=tags or [],
    )

    if existing and existing.content_hash == content_hash:
        # Texte identique : pas de ré-embedding (coût), seules les métadonnées évoluent.
        for k, v in meta.items():
            setattr(existing, k, v)
        db.execute(
            update(DocumentChunk)
            .where(DocumentChunk.document_id == existing.id)
            .values(reliability_score=reliability_score)
        )
        if commit:
            db.commit()
        n = len(existing.chunks)
        return IngestResult(existing.id, n, "unchanged")

    pieces = chunk_text(text, settings.chunk_size, settings.chunk_overlap)
    # L'appel réseau se fait AVANT toute écriture : un échec Gemini ne laisse rien de partiel.
    vectors = llm.embed([f"{title}\n\n{p}" for p in pieces], "RETRIEVAL_DOCUMENT")

    if existing:
        doc = existing
        doc.chunks.clear()
        db.flush()
        status = "updated"
    else:
        doc = Document(external_id=external_id, content="", content_hash="", **meta)
        db.add(doc)
        status = "created"
    for k, v in meta.items():
        setattr(doc, k, v)
    doc.content = text
    doc.content_hash = content_hash
    doc.chunks = [
        DocumentChunk(
            chunk_index=i,
            text=p,
            embedding=to_blob(v),
            embedding_model=settings.embedding_model,
            reliability_score=reliability_score,
        )
        for i, (p, v) in enumerate(zip(pieces, vectors))
    ]
    db.flush()
    if commit:
        db.commit()
    return IngestResult(doc.id, len(pieces), status)
