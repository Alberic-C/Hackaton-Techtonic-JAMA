"""Recherche vectorielle : cosinus exact en mémoire (NumPy) sur des embeddings stockés en SQLite.

- SQLite reste la source de vérité (embeddings en BLOB float32 normalisés).
- Une matrice (N, d) est chargée paresseusement ; un « tampon » (count, max id, somme des scores)
  est relu à chaque recherche (1 requête agrégée) -> rechargement auto si un autre process a écrit.
- Le score de fiabilité agit deux fois : filtre dur (seuil) puis pondération du classement.
- L'interface `search()` est isolée : passer à sqlite-vec / FAISS ne touche pas le reste.
"""
import threading
from dataclasses import dataclass

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Document, DocumentChunk


def to_blob(vec: list[float] | np.ndarray) -> bytes:
    v = np.asarray(vec, dtype=np.float32)
    n = np.linalg.norm(v)
    return (v / n if n > 0 else v).astype(np.float32).tobytes()


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    title: str
    text: str
    source_type: str
    author: str | None
    reliability_score: int
    similarity: float
    score: float  # similarité pondérée par la fiabilité


class NumpyVectorStore:
    # Un document 100% fiable garde sa similarité ; un document à 0 la voit divisée par 2.
    RELIABILITY_FLOOR = 0.5

    def __init__(self, embedding_model: str, dim: int):
        self.embedding_model = embedding_model
        self.dim = dim
        self._lock = threading.Lock()
        self._stamp: tuple | None = None
        self._ids = np.empty(0, dtype=np.int64)
        self._rel = np.empty(0, dtype=np.int32)
        self._mat = np.empty((0, dim), dtype=np.float32)

    def _current_stamp(self, db: Session) -> tuple:
        row = db.execute(
            select(
                func.count(DocumentChunk.id),
                func.coalesce(func.max(DocumentChunk.id), 0),
                func.coalesce(func.sum(DocumentChunk.reliability_score), 0),
            ).where(DocumentChunk.embedding_model == self.embedding_model)
        ).one()
        return tuple(row)

    def _ensure_loaded(self, db: Session) -> None:
        stamp = self._current_stamp(db)
        with self._lock:
            if stamp == self._stamp:
                return
            rows = db.execute(
                select(DocumentChunk.id, DocumentChunk.reliability_score, DocumentChunk.embedding).where(
                    DocumentChunk.embedding_model == self.embedding_model
                )
            ).all()
            ids, rel, vecs = [], [], []
            for cid, r, blob in rows:
                v = np.frombuffer(blob, dtype=np.float32)
                if v.shape[0] != self.dim:  # embedding d'une autre dimension : ignoré
                    continue
                ids.append(cid)
                rel.append(r)
                vecs.append(v)
            self._ids = np.asarray(ids, dtype=np.int64)
            self._rel = np.asarray(rel, dtype=np.int32)
            self._mat = np.vstack(vecs) if vecs else np.empty((0, self.dim), dtype=np.float32)
            self._stamp = stamp

    def search(
        self,
        db: Session,
        query_vec: list[float],
        top_k: int = 6,
        min_reliability: int = 0,
        min_similarity: float = 0.0,
    ) -> list[RetrievedChunk]:
        self._ensure_loaded(db)
        with self._lock:
            ids, rel, mat = self._ids, self._rel, self._mat  # snapshot immuable (remplacé, jamais muté)
        if ids.size == 0:
            return []

        q = np.asarray(query_vec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)

        mask = rel >= min_reliability
        if not mask.any():
            return []
        cand_idx = np.flatnonzero(mask)
        sims = mat[cand_idx] @ q  # cosinus (vecteurs déjà normalisés)
        weights = self.RELIABILITY_FLOOR + (1 - self.RELIABILITY_FLOOR) * rel[cand_idx] / 100.0
        scores = sims * weights

        order = np.argsort(-scores)[:top_k]
        order = [i for i in order if sims[i] >= min_similarity]
        if not order:
            return []

        chosen_ids = [int(ids[cand_idx[i]]) for i in order]
        by_id = {
            c.id: (c, d)
            for c, d in db.execute(
                select(DocumentChunk, Document)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(DocumentChunk.id.in_(chosen_ids))
            ).all()
        }
        results = []
        for i in order:
            c, d = by_id[int(ids[cand_idx[i]])]
            results.append(
                RetrievedChunk(
                    chunk_id=c.id,
                    document_id=d.id,
                    title=d.title,
                    text=c.text,
                    source_type=d.source_type.value,
                    author=d.author,
                    reliability_score=c.reliability_score,
                    similarity=float(sims[i]),
                    score=float(scores[i]),
                )
            )
        return results
