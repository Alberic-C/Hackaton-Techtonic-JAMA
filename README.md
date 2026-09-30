# Hackaton Techtonic JAMA
 
 Summary : 
The goal of this application is to help the user in his thinking process in order to allow him to get the best result out of the information currently at his disposal. 
To do that, we used a popular methodology for resolving problems, popularized by the blog “Farhnam Street”, “Shane Perrish” and “Charlie Munger”

This framework follows the following steps, that are incentivized by the agent : 
Define the problem
Is this problem the root problem ?
What is the goal we’re looking for ?
What are the obstacles ? 
Explore the possible solutions 
What is going to happen in the best case scenario and the worst case scenario ? Are the unwanted results fixable ? 
What will this solution result in the long run ? 
Is it possible to choose multiples solutions at the same time ? 
What would you do if this solution wasn’t possible ? 
Evaluate the options 
Is this information coming from a verified authority (Official document from the company ? Who is the author ?) 
What is the cost of opportunity for this solution ? 
Considering the pros and cons, what is the most convenient solution ? 
Security Margin 

Learn from these decisions 
Give a feedback to the database that will be taken into consideration for the next decisions. 

How to run it 
Interact with the conversational agent


What wasn’t implemented ? 
Online research 
Possibility to add files 
Implementention of the report for the future decisions
 
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
