# Install Sophia locally on another PC
Prerequisites: Python 3.11+ (developed on 3.14), Git, and a Google Gemini API key.

# 1. Clone the repository
`git clone https://github.com/Alberic-C/Hackaton-Techtonic-JAMA.git`

cd Hackaton-Techtonic-JAMA
# 2. Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # use requirements-dev.txt if you want to run the tests
# 3. Configure environment variables
cp .env.example .env               # Windows: copy .env.example .env
Edit .env and set at least:

GEMINI_API_KEY=your_gemini_key
# 4. Start the server
uvicorn app.main:app --reload
Open http://127.0.0.1:8000 for the UI, or http://127.0.0.1:8000/docs for the API docs.

Optional checks

Load a sample document, as n8n would:
curl -X POST http://127.0.0.1:8000/webhooks/n8n/documents \
     -H "X-API-Key: <your WEBHOOK_API_KEY>" -H "Content-Type: application/json" \
     -d @scripts/sample_n8n_payload.json
Run the tests (no API key needed, Gemini is mocked): pytest

## Troubleshooting

Error 503: Google's models are temporarily overloaded. The app retries and falls back to other models automatically. Wait a few seconds and resend.
Error 404 on a model: some models are not available to new accounts. Change GEMINI_MODEL in .env (for example to gemini-3.6-flash).
.env and sophia.db: the database is created automatically on first start. Neither file is committed to git, so never share your .env.
