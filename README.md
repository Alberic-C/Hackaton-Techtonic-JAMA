# Hackaton Techtonic JAMA
 
## instructions données
### [Aikido]
10% de la note vient de à quel point le projet est sécure
points calculés sur base du nombre de bugs trouvé par [l'outil d'aikido](https://app.aikido.dev/queue?questionnaire=1) (connecté au repo du hackaton)
### KBC
- ils veulent un proof of concept
- facile la ou on a accepté la difficulté
- scalable

### SD worx
ils ont 100+ payrroll services
poc : faire en sorte que ce soit plus facile de trouver partager et avoir confiance en de l'information


---

# Sophia — prototype

Plateforme d'aide à la décision (méthode *Clear Thinking*) : n8n alimente une base documentaire vectorisée (SQLite),
un agent Gemini guide l'utilisateur en 5 étapes avec garde-fous, et les retours d'expérience réinjectés améliorent le RAG.
Détails et schéma : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · prompts : [app/prompts.py](app/prompts.py).

## Lancer
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # renseigner GEMINI_API_KEY et WEBHOOK_API_KEY
uvicorn app.main:app --reload
```
- UI de test : http://localhost:8000 · API : http://localhost:8000/docs
- Ingérer un document comme le ferait n8n :
```bash
curl -X POST localhost:8000/webhooks/n8n/documents -H "X-API-Key: $WEBHOOK_API_KEY" \
     -H "Content-Type: application/json" -d @scripts/sample_n8n_payload.json
```
- Tests (sans clé API, Gemini est simulé) : `pytest`

## Endpoints
| Méthode | Route | Rôle |
|---|---|---|
| POST | `/webhooks/n8n/documents` | Ingestion n8n (clé `X-API-Key`) |
| POST | `/api/sessions` | Nouvelle décision (ouvre l'étape 1) |
| POST | `/api/sessions/{id}/messages` | Tour de conversation |
| POST | `/api/sessions/{id}/advance` | Étape suivante (422 si garde-fou non satisfait) |
| GET | `/api/sessions[/{id}]` | Liste / détail + état |
| GET | `/api/reviews/pending` | Décisions en attente d'évaluation |
| POST | `/api/reviews` | **Étape 5** : rapport d'évaluation → leçon vectorisée |
