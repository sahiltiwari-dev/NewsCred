# EvidenceCompass — Streamlit

This build is prepared for **Streamlit Community Cloud**.

## Entry point

`streamlit_app.py`

## Local run

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Optional Google Fact Check API

Add this in Streamlit Community Cloud → App settings → Secrets:

```toml
GOOGLE_FACTCHECK_API_KEY = "your-key"
```

The app also works without this key using Google News RSS and GDELT.

## Streamlit Community Cloud

Repository root should contain:

- `streamlit_app.py`
- `news_engine.py`
- `requirements.txt`
- `.streamlit/config.toml`

Create the app with entrypoint `streamlit_app.py`.

Do **not** upload `.venv/` or `venv/`.
