from pathlib import Path
import json
import re
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FILE = BASE_DIR / "data" / "evidence_records.json"

app = FastAPI(
    title="Evidence Compass API",
    description="Prototype API for Team Stackers' news-evidence credibility assistant.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ClaimRequest(BaseModel):
    claim: str = Field(min_length=5, max_length=1000)
    url: Optional[str] = None


def load_records():
    with DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", text.lower()).strip()


def token_set(text: str):
    return {token for token in normalize(text).split() if len(token) > 2}


def similarity(a: str, b: str) -> float:
    left, right = token_set(a), token_set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def find_record(claim: str, records):
    # First, allow a direct demo keyword match.
    claim_norm = normalize(claim)
    best = None
    best_score = 0.0
    for record in records:
        score = similarity(claim, record["claim"])
        for keyword in record.get("keywords", []):
            if keyword.lower() in claim_norm:
                score += 0.18
        if score > best_score:
            best = record
            best_score = score

    if best is None or best_score < 0.16:
        return None
    return best


def score_record(record):
    signals = []
    score = 0

    if record["original_source_available"]:
        score += 2
        signals.append({"label": "Original / primary source", "points": 2})
    else:
        score -= 2
        signals.append({"label": "No original-source clue", "points": -2})

    if record["independent_supporting_sources"] > 0:
        score += 1
        signals.append({"label": "Independent supporting source", "points": 1})
    else:
        signals.append({"label": "Independent supporting source", "points": 0})

    if record["context_match"]:
        score += 1
        signals.append({"label": "Date / context match", "points": 1})
    else:
        score -= 1
        signals.append({"label": "Stale / out-of-context", "points": -1})

    if record["conflicting_sources_count"] > 0:
        score -= 2
        signals.append({"label": "Conflicting evidence", "points": -2})
    else:
        signals.append({"label": "Conflicting evidence", "points": 0})

    if score >= 3:
        level = "Higher support"
    elif score >= 1:
        level = "Mixed"
    else:
        level = "Low support"

    return score, level, signals


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Evidence Compass API"}


@app.get("/api/records")
def records():
    return {"count": len(load_records()), "records": load_records()}


@app.post("/api/check")
def check_claim(request: ClaimRequest):
    records = load_records()
    record = find_record(request.claim, records)
    if not record:
        raise HTTPException(
            status_code=404,
            detail="No close demo evidence record was found. Try one of the sample claims shown in the interface."
        )

    score, level, signals = score_record(record)

    reasons = []
    if record["original_source_available"]:
        reasons.append("An original/primary-source clue is available (+2).")
    else:
        reasons.append("No original-source clue is available (-2).")
    if record["independent_supporting_sources"] > 0:
        reasons.append(f"{record['independent_supporting_sources']} independent supporting source(s) are represented (+1).")
    else:
        reasons.append("No independent supporting source is represented (0).")
    if record["context_match"]:
        reasons.append("The record's date/context matches the claim framing (+1).")
    else:
        reasons.append("The record has a freshness/context concern (-1).")
    if record["conflicting_sources_count"] > 0:
        reasons.append(f"{record['conflicting_sources_count']} conflicting source(s) are represented (-2).")
    else:
        reasons.append("No conflicting source is represented (0).")

    explanation = record["explanation"]
    return {
        "support_level": level,
        "score": score,
        "record_title": record["title"],
        "matched_claim": record["claim"],
        "original_source_available": record["original_source_available"],
        "original_source_clue": record["original_source_clue"],
        "context_match": record["context_match"],
        "context_note": record["context_note"],
        "signals": signals,
        "reasons": reasons,
        "supporting_sources": record["supporting_sources"],
        "conflicting_sources": record["conflicting_sources"],
        "explanation": explanation,
        "input_url_received": bool(request.url),
    }
