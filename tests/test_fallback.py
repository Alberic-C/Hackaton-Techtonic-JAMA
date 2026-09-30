import pytest
from google.genai import errors

from app.config import Settings
from app.gemini_client import GeminiClient, LLMUnavailable
from app.schemas import AgentTurn


class _Resp:
    parsed = AgentTurn(reply="ok")
    text = '{"reply":"ok"}'


class _Models:
    def __init__(self, failing):
        self.failing, self.used = failing, []

    def generate_content(self, model, contents, config):
        self.used.append(model)
        if model in self.failing:
            raise errors.ServerError(503, {"error": {"message": "overloaded"}})
        return _Resp()


def _client(failing, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    c = GeminiClient(Settings(gemini_api_key="k", gemini_model="A", gemini_fallback_models="B,C"))
    c._client = type("X", (), {"models": _Models(failing)})()
    return c


def test_falls_back_to_next_model(monkeypatch):
    c = _client({"A"}, monkeypatch)
    from app.gemini_client import ChatTurn
    out = c.generate_structured("sys", [ChatTurn(role="user", text="hi")], AgentTurn)
    assert out.reply == "ok" and c._client.models.used == ["A", "A", "B"]


def test_all_models_down(monkeypatch):
    c = _client({"A", "B", "C"}, monkeypatch)
    from app.gemini_client import ChatTurn
    with pytest.raises(LLMUnavailable):
        c.generate_structured("sys", [ChatTurn(role="user", text="hi")], AgentTurn)
