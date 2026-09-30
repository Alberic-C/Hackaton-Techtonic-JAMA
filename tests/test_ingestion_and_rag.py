import copy

H = {"X-API-Key": "test-key"}


def payload(**over):
    p = {
        "external_id": "doc-1",
        "title": "Réduire le churn client",
        "text": "Le churn augmente à cause d'un onboarding trop long. Un pilote d'onboarding guidé a réduit le churn de 20%.",
        "metadata": {"author": "Alice", "context": "Customer Success", "date": "2026-03-01T10:00:00Z", "tags": ["churn"]},
        "reliability_score": 95,
    }
    p.update(over)
    return p


def test_webhook_requires_key(client):
    assert client.post("/webhooks/n8n/documents", json=payload()).status_code == 401
    assert client.post("/webhooks/n8n/documents", json=payload(), headers={"X-API-Key": "bad"}).status_code == 401


def test_webhook_validation_is_strict(client):
    bad = payload(reliability_score=101)
    assert client.post("/webhooks/n8n/documents", json=bad, headers=H).status_code == 422
    extra = payload(unknown="x")
    assert client.post("/webhooks/n8n/documents", json=extra, headers=H).status_code == 422


def test_webhook_idempotent(client, llm):
    r = client.post("/webhooks/n8n/documents", json=payload(), headers=H)
    assert r.status_code == 201 and r.json()["status"] == "created"
    calls = llm.calls
    r2 = client.post("/webhooks/n8n/documents", json=payload(reliability_score=90), headers=H)
    assert r2.status_code == 200 and r2.json()["status"] == "unchanged"
    assert llm.calls == calls  # pas de ré-embedding
    r3 = client.post("/webhooks/n8n/documents", json=payload(text="x" * 50), headers=H)
    assert r3.json()["status"] == "updated"


def test_search_filters_and_weights_by_reliability(client, llm):
    from app.database import SessionLocal
    from app.deps import get_store

    same = payload()["text"]
    client.post("/webhooks/n8n/documents", json=payload(external_id="a", reliability_score=95), headers=H)
    client.post("/webhooks/n8n/documents", json=payload(external_id="b", title="Web", text=same + " ", reliability_score=30), headers=H)
    store = get_store()
    q = llm.embed(["churn onboarding pilote"], "RETRIEVAL_QUERY")[0]
    with SessionLocal() as db:
        hits = store.search(db, q, top_k=5, min_reliability=0)
        assert [h.reliability_score for h in hits] == [95, 30]  # même similarité -> fiabilité départage
        assert [h.reliability_score for h in store.search(db, q, min_reliability=40)] == [95]
