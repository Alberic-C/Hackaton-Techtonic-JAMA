"""Appels natifs au SDK officiel `google-genai`. Aucune couche d'orchestration."""
import time
from collections.abc import Callable
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Settings

T = TypeVar("T", bound=BaseModel)
TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
EMBED_BATCH = 50
RETRYABLE = {429, 500, 503, 504}


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    """Service surchargé / indisponible (429, 5xx) après retries : un modèle de repli peut aider."""


class ChatTurn(BaseModel):
    role: Literal["user", "model"]
    text: str


class LLMClient(Protocol):
    """Interface minimale : facile à remplacer par un faux client dans les tests."""

    def embed(self, texts: list[str], task_type: TaskType) -> list[list[float]]: ...

    def generate_structured(self, system: str, turns: list[ChatTurn], schema: type[T]) -> T: ...


class GeminiClient:
    def __init__(self, settings: Settings):
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY n'est pas configurée")
        from google import genai  # import tardif : les tests n'en ont pas besoin

        self._s = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    # -- robustesse réseau ---------------------------------------------------
    def _call(self, fn: Callable, attempts: int = 2):
        from google.genai import errors

        for i in range(attempts):
            try:
                return fn()
            except errors.APIError as e:
                if e.code in RETRYABLE:
                    if i < attempts - 1:
                        time.sleep(2**i)
                        continue
                    raise LLMUnavailable(f"Gemini surchargé ({e.code}), réessaie dans un instant") from e
                raise LLMError(f"Erreur API Gemini ({e.code})") from e
            except Exception as e:  # réseau, timeout...
                raise LLMError("Appel Gemini impossible") from e

    # -- embeddings ----------------------------------------------------------
    def embed(self, texts: list[str], task_type: TaskType) -> list[list[float]]:
        from google.genai import types

        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]
            resp = self._call(
                lambda: self._client.models.embed_content(
                    model=self._s.embedding_model,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type, output_dimensionality=self._s.embedding_dim
                    ),
                )
            )
            out.extend(list(e.values) for e in resp.embeddings)
        if len(out) != len(texts):
            raise LLMError("Nombre d'embeddings inattendu")
        return out

    # -- génération JSON structurée ------------------------------------------
    def generate_structured(self, system: str, turns: list[ChatTurn], schema: type[T]) -> T:
        from google.genai import types

        contents = [
            types.Content(role=t.role, parts=[types.Part.from_text(text=t.text)]) for t in turns
        ]
        resp = None
        last: LLMUnavailable | None = None
        for model in self._s.gemini_model_chain:  # repli automatique si le modèle est surchargé
            try:
                resp = self._call(
                    lambda m=model: self._client.models.generate_content(
                        model=m,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system,
                            response_mime_type="application/json",
                            response_schema=schema,
                            temperature=0.4,
                        ),
                    )
                )
                break
            except LLMUnavailable as e:
                last = e
        if resp is None:
            raise last or LLMError("Aucun modèle Gemini disponible")
        try:
            if isinstance(resp.parsed, schema):
                return resp.parsed
            return schema.model_validate_json(resp.text)
        except (ValidationError, ValueError, TypeError) as e:
            raise LLMError("Réponse Gemini non conforme au schéma") from e
