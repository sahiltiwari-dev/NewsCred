# NewsCred launch checklist

## 1. Test locally

```powershell
cd NewsCred
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

Test:
- normal claim
- article URL
- empty input
- very long input
- History
- Clear History
- About
- Privacy
- `/health`

## 2. Push to GitHub

Create a new repository, then from the NewsCred folder:

```powershell
git init
git add .
git commit -m "Prepare NewsCred for production"
git branch -M main
git remote add origin YOUR_REPOSITORY_URL
git push -u origin main
```

Do not commit `.env` or API keys.

## 3. Deploy on Render

Create a new Web Service from the GitHub repository.

Use:

Build Command:

```text
pip install -r requirements.txt
```

Start Command:

```text
gunicorn app:app --workers 2 --threads 4 --timeout 60
```

Add:

```text
PYTHON_VERSION=3.13.5
SESSION_COOKIE_SECURE=1
RATE_LIMIT_PER_MINUTE=12
SECRET_KEY=<generate a strong secret>
DATABASE_URL=<your PostgreSQL connection string>
```

Render's Flask deployment documentation recommends Gunicorn for production and supports automatic deployments from a connected Git repository.

## 4. Optional fact-check integration

Create a Google Fact Check Tools API key and set:

```text
GOOGLE_FACTCHECK_API_KEY=<your key>
```

NewsCred will then query Google's `claims:search` endpoint and include available published fact-check reviews in the evidence set.

## 5. Before public launch

- Add a custom domain.
- Confirm HTTPS works.
- Test on a phone.
- Test rate limiting.
- Test article URL fetching.
- Read the privacy notice and adapt it to your actual organization/data practices.
- Do not advertise the score as a guarantee of truth.
- Keep the source links visible so users can inspect evidence themselves.
- Monitor application logs and failed searches.

## 6. What this version intentionally does not do

- It does not claim that repeated headlines prove truth.
- It does not automatically declare every claim true or false.
- It does not require user accounts.
- Anonymous history is tied to a signed browser session.
- Search availability depends on external public news services.
