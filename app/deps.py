import hmac
from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings
from .gemini_client import GeminiClient, LLMClient
from .vector_store import NumpyVectorStore


@lru_cache
def get_llm() -> LLMClient:
    return GeminiClient(get_settings())


@lru_cache
def get_store() -> NumpyVectorStore:
    s = get_settings()
    return NumpyVectorStore(s.embedding_model, s.embedding_dim)


def require_webhook_key(
    x_api_key: str = Header(default=""), settings: Settings = Depends(get_settings)
) -> None:
    expected = settings.webhook_api_key
    # Fail closed : sans clé configurée, rien ne passe. Comparaison à temps constant.
    if not expected or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé API invalide")
