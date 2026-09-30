from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./sophia.db"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    # Modèles de repli (séparés par des virgules) si le principal renvoie 429/5xx de façon persistante.
    gemini_fallback_models: str = "gemini-3.6-flash,gemini-flash-lite-latest"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768

    # Secret partagé n8n -> FastAPI. Vide = webhook fermé (fail closed).
    webhook_api_key: str = ""

    # RAG
    rag_top_k: int = 6
    rag_min_reliability: int = 40  # filtre dur appliqué à l'étape 2
    chunk_size: int = 1200
    chunk_overlap: int = 150
    feedback_reliability: int = 85  # score attribué aux leçons issues des reviews

    max_history_messages: int = 30

    @property
    def gemini_model_chain(self) -> list[str]:
        extra = [m.strip() for m in self.gemini_fallback_models.split(",") if m.strip()]
        return [self.gemini_model, *[m for m in extra if m != self.gemini_model]]


@lru_cache
def get_settings() -> Settings:
    return Settings()
