import hashlib
import os
import re
import tempfile

import numpy as np

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["WEBHOOK_API_KEY"] = "test-key"
os.environ["EMBEDDING_DIM"] = "64"
os.environ["GEMINI_API_KEY"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.deps import get_llm  # noqa: E402
from app.main import app  # noqa: E402
from app.schemas import AgentTurn, ReviewLesson  # noqa: E402


class FakeLLM:
    """Embeddings « sac de mots » hachés (similarité réelle) + tours scriptés."""

    def __init__(self):
        self.turns: list[AgentTurn] = []
        self.calls = 0

    def embed(self, texts, task_type):
        self.calls += 1
        out = []
        for t in texts:
            v = np.zeros(64, dtype=np.float32)
            for w in re.findall(r"\w+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
            out.append(v.tolist())
        return out

    def generate_structured(self, system, turns, schema):
        self.last_system = system
        if schema is ReviewLesson:
            return ReviewLesson(
                headline="Le pilote a réduit le churn",
                outcome_vs_expectation="Meilleur que prévu",
                decision_quality_assessment="Processus solide",
                what_worked=["onboarding"],
                what_failed=[],
                key_lessons=["mesurer tôt"],
                advice_for_similar_cases=["pilote de 2 semaines"],
                context_tags=["churn"],
            )
        return self.turns.pop(0)


@pytest.fixture
def llm():
    f = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: f
    yield f
    app.dependency_overrides.clear()


@pytest.fixture
def client(llm):
    from app.database import engine
    from app.models import Base

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c
