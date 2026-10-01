# EvidenceCompass

EvidenceCompass is a evidence-credibility verification web application. It searches recent news coverage, compares claim wording with headlines, identifies conflict/fact-check language, evaluates source quality, and produces an explainable **verification confidence** score.

> Important: a score is not a mathematical probability that a claim is true. News coverage can repeat the same mistake. Users should open the cited source and check the original/authoritative source before sharing important claims.

## Run locally

### 1. Create a virtual environment

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 3. Run

```powershell
python app.py
```

Open `http://127.0.0.1:5000`.

## Production

Use Gunicorn rather than Flask's development server:

```bash
gunicorn app:app --workers 2 --threads 4 --timeout 60
```

Set these environment variables in production:

- `SECRET_KEY`: a long random secret
- `DATABASE_URL`: PostgreSQL connection string
- `RATE_LIMIT_PER_MINUTE`: request limit per IP per minute
- `GOOGLE_FACTCHECK_API_KEY`: optional Google Fact Check Tools API key; when configured, published fact-check reviews are added to the evidence set. Google documents the `claims:search` endpoint for searching fact-checked claims.

The included `render.yaml` is a starting point for Render deployment.

## Data and privacy

The app stores anonymous verification history so users can see recent checks from the same browser session. There is no account system in this version. Clearing the browser's cookies creates a new anonymous history identity.

Before a public launch, publish a privacy notice and terms appropriate to your intended audience.
