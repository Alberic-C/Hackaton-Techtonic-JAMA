from app.agent import apply_turn, missing_for_advance
from app.schemas import (ActionItem, AgentTurn, Evaluation, Scenario, SessionState, Solution)

H = {"X-API-Key": "test-key"}


def test_root_cause_cannot_be_proposed_and_confirmed_in_one_turn():
    s = apply_turn(SessionState(), AgentTurn(reply="", problem_statement="P", root_cause="R", root_cause_confirmed=True), 1)
    assert s.root_cause == "R" and not s.root_cause_confirmed
    s = apply_turn(s, AgentTurn(reply="", root_cause_confirmed=True), 1)
    assert s.root_cause_confirmed
    s = apply_turn(s, AgentTurn(reply="", root_cause="R2"), 1)  # cause modifiée -> reconfirmer
    assert not s.root_cause_confirmed


def test_fields_outside_step_are_ignored():
    s = apply_turn(SessionState(), AgentTurn(reply="", solutions=[Solution(title="A", description="d")]), 1)
    assert s.solutions == []


def test_step2_requires_all_three_scenarios():
    st = SessionState(solutions=[Solution(title="A", description="d", selected=True)],
                      scenarios=[Scenario(solution_title="a", best_case="x", worst_case="y")])
    assert "plan si ça échoue" in missing_for_advance(st, 2)[0]


def test_full_journey(client, llm):
    client.post("/webhooks/n8n/documents", headers=H, json={
        "title": "Pilote onboarding", "text": "Le churn client a baissé après un onboarding guidé en 2 semaines. " * 3,
        "metadata": {"author": "A", "context": "CS", "date": "2026-01-01T00:00:00Z"}, "reliability_score": 90})

    sol = Solution(title="Pilote onboarding", description="d", selected=True)
    scen = Scenario(solution_title="Pilote onboarding", best_case="-20%", worst_case="0%", if_it_fails="rollback")
    plan = ActionItem(action="Lancer", owner="Bob", deadline="J+2", success_metric="NPS")
    # Un élément par appel LLM, dans l'ordre réel.
    llm.turns += [
        AgentTurn(reply="Quel est le problème ?"),                                        # ouverture étape 1
        AgentTurn(reply="Symptôme ou racine ?", problem_statement="Churn élevé", root_cause="Onboarding trop long"),
        AgentTurn(reply="OK racine", root_cause_confirmed=True),
        AgentTurn(reply="Voici des pistes [S1]", solutions=[sol]),                        # ouverture étape 2
        AgentTurn(reply="Scénarios enregistrés", scenarios=[scen]),
        AgentTurn(reply="Comparons"),                                                     # ouverture étape 3
        AgentTurn(reply="Eval", evaluations=[Evaluation(solution_title="Pilote onboarding", pros=["rapide"], cons=["coût"])], chosen_solution="Pilote onboarding"),
        AgentTurn(reply="Voici un plan", action_plan=[plan]),                             # ouverture étape 4
        AgentTurn(reply="Validé", decision_validated=True),
        AgentTurn(reply="Résumé", decision_summary="# Décision"),                         # ouverture étape 5
    ]

    sid = client.post("/api/sessions", json={"title": "Churn"}).json()["session_id"]
    post = lambda t: client.post(f"/api/sessions/{sid}/messages", json={"content": t})
    adv = lambda: client.post(f"/api/sessions/{sid}/advance")

    # Garde-fou étape 1 : impossible d'avancer sans confirmation
    post("Le churn augmente")
    r = adv()
    assert r.status_code == 422 and r.json()["detail"]["missing"]
    post("Oui c'est la racine")

    r = adv()                                   # -> étape 2 avec RAG
    assert r.status_code == 200 and r.json()["step"] == 2
    assert r.json()["sources"] and r.json()["sources"][0]["reliability_score"] == 90
    assert "Pilote onboarding" in llm.last_system and "<sources>" in llm.last_system

    assert adv().status_code == 422             # scénarios manquants
    post("Je retiens le pilote ; pire cas 0%, plan B rollback")
    assert adv().json()["step"] == 3

    post("Je choisis le pilote")
    assert adv().json()["step"] == 4
    post("Oui je valide")
    r = adv()
    assert r.json()["step"] == 5 and r.json()["status"] == "awaiting_review"
    assert client.post(f"/api/sessions/{sid}/messages", json={"content": "hey"}).status_code == 409

    # --- Étape 5 asynchrone : review -> leçon vectorisée -> retrouvable par le RAG
    assert [s["id"] for s in client.get("/api/reviews/pending").json()] == [sid]
    rv = client.post("/api/reviews", json={"session_id": sid, "outcome": "success",
                                           "report": "Le pilote a réduit le churn de 18% en 6 semaines."})
    assert rv.status_code == 201 and rv.json()["document_id"]
    assert client.post("/api/reviews", json={"session_id": sid, "outcome": "success",
                                             "report": "Deuxième rapport pour la même décision."}).status_code == 409
    assert client.get(f"/api/sessions/{sid}").json()["status"] == "reviewed"

    from app.database import SessionLocal
    from app.deps import get_store
    with SessionLocal() as db:
        hits = get_store().search(db, llm.embed(["pilote churn onboarding"], "RETRIEVAL_QUERY")[0], min_reliability=80)
        assert any(h.source_type == "feedback" for h in hits)


def test_review_rejected_before_step5(client, llm):
    from app.schemas import AgentTurn
    llm.turns.append(AgentTurn(reply="hi"))
    sid = client.post("/api/sessions", json={}).json()["session_id"]
    r = client.post("/api/reviews", json={"session_id": sid, "outcome": "failure", "report": "x" * 30})
    assert r.status_code == 409
