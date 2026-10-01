import ipaddress
import logging
import json
import secrets
import os
import re
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix


# ============================================================
# NEWS CRED — production-oriented Flask application
# ============================================================

app = Flask(__name__)
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY and os.environ.get("FLASK_ENV", "development").lower() == "production":
    raise RuntimeError("SECRET_KEY must be set in production.")
app.config["SECRET_KEY"] = SECRET_KEY or "dev-only-change-me"
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL",
    "sqlite:///newscred.db",
).replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # 16 KB request body limit
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

# Render sits behind a proxy. This lets Flask see the real client IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Production logging.
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("evidencecompass")

db = SQLAlchemy(app)


class Check(db.Model):
    __tablename__ = "checks"

    id = db.Column(db.Integer, primary_key=True)
    visitor_id = db.Column(db.String(64), nullable=False, index=True)
    claim = db.Column(db.Text, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    result = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(32), nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )


class ShareResult(db.Model):
    __tablename__ = "share_results"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(32), unique=True, nullable=False, index=True)
    payload = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    expires_at = db.Column(db.DateTime, nullable=False, index=True)


with app.app_context():
    db.create_all()


# ============================================================
# CONSTANTS
# ============================================================

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "has", "have", "had",
    "will", "would", "could", "should", "this", "that", "these", "those",
    "to", "of", "in", "on", "for", "and", "or", "with", "from", "by", "as",
    "at", "it", "its", "be", "been", "being", "about", "into", "after",
    "before", "every", "all", "new", "than", "their", "they", "them", "you",
    "your", "our", "we", "he", "she", "his", "her", "who", "what", "when",
    "where", "why", "how", "says", "said", "according", "report", "reports",
}

CONFLICT_TERMS = {
    "false", "fake", "falsely", "debunked", "debunk", "misleading", "misinformation",
    "disinformation", "denies", "denied", "deny", "no evidence", "untrue",
    "incorrect", "hoax", "scam", "fact check", "fact-check", "clarification",
}

AUTHORITATIVE_DOMAINS = {
    "pib.gov.in", "factcheck.pib.gov.in", "gov.in", "nic.in", "eci.gov.in",
    "rbi.org.in", "sebi.gov.in", "supremecourt.gov.in", "who.int", "un.org",
    "nasa.gov", "cdc.gov", "fda.gov", "europa.eu",
}

MAJOR_NEWS_DOMAINS = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "theguardian.com",
    "nytimes.com", "washingtonpost.com", "cnn.com", "aljazeera.com",
    "hindustantimes.com", "indianexpress.com", "thehindu.com", "ndtv.com",
    "timesofindia.indiatimes.com", "economictimes.indiatimes.com",
}

# Simple in-process rate limiter. It protects a small deployment from accidental
# abuse without requiring another service. For large scale, replace with Redis.
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "12"))
_rate_log = {}


# ============================================================
# HELPERS
# ============================================================

def get_visitor_id():
    visitor_id = session.get("visitor_id")
    if not visitor_id:
        visitor_id = uuid.uuid4().hex
        session["visitor_id"] = visitor_id
    return visitor_id


def client_ip():
    return request.remote_addr or "unknown"


def rate_limited():
    now = time.time()
    ip = client_ip()
    events = [t for t in _rate_log.get(ip, []) if now - t < 60]
    if len(events) >= RATE_LIMIT:
        _rate_log[ip] = events
        return True
    events.append(now)
    _rate_log[ip] = events
    # Keep the dictionary bounded.
    if len(_rate_log) > 5000:
        oldest = sorted(_rate_log, key=lambda k: _rate_log[k][-1] if _rate_log[k] else 0)[:1000]
        for key in oldest:
            _rate_log.pop(key, None)
    return False


def normalize_text(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def tokenize(text):
    words = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9'-]+", (text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in STOP_WORDS}


def compare_claim_with_headline(claim, headline):
    claim_words = tokenize(claim)
    headline_words = tokenize(headline)

    if not claim_words:
        return {"relation": "insufficient", "matched_words": 0, "match_percentage": 0}

    common = claim_words & headline_words
    percentage = round((len(common) / len(claim_words)) * 100)

    if percentage >= 50:
        relation = "related"
    elif percentage >= 25:
        relation = "weakly_related"
    else:
        relation = "insufficient"

    return {
        "relation": relation,
        "matched_words": len(common),
        "match_percentage": percentage,
    }


def domain_from_url(url):
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def source_quality(domain):
    domain = (domain or "").lower().removeprefix("www.")
    if not domain:
        return 0.35, "Unknown source"

    if any(domain == d or domain.endswith("." + d) for d in AUTHORITATIVE_DOMAINS):
        return 1.0, "Authoritative / primary source"

    if any(domain == d or domain.endswith("." + d) for d in MAJOR_NEWS_DOMAINS):
        return 0.8, "Established news publisher"

    if domain.endswith(".gov") or domain.endswith(".gov.in") or domain.endswith(".nic.in"):
        return 0.95, "Government source"

    return 0.5, "News/web source"


def conflict_signal(title):
    lower = (title or "").lower()
    return any(term in lower for term in CONFLICT_TERMS)


def clean_query(claim):
    words = []
    for word in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9'-]+", claim.lower()):
        if len(word) >= 4 and word not in STOP_WORDS:
            words.append(word)
    # Preserve order and remove duplicates.
    unique = list(dict.fromkeys(words))
    return " ".join(unique[:10]) or claim[:150]


def is_safe_public_url(url):
    """Reject local/private/link-local targets before fetching user-supplied URLs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname
        if host.lower() in {"localhost", "localhost.localdomain"}:
            return False
        infos = socket.getaddrinfo(host, None)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def extract_article_text(url):
    if not is_safe_public_url(url):
        raise ValueError("That URL cannot be fetched safely. Paste the headline or claim instead.")

    response = requests.get(
        url,
        timeout=(4, 8),
        allow_redirects=True,
        headers={"User-Agent": "EvidenceCompass/1.0"},
    )
    response.raise_for_status()

    # Re-check the final destination after redirects to reduce SSRF risk.
    if not is_safe_public_url(response.url):
        raise ValueError("That article redirects to a URL that cannot be fetched safely.")

    if "text/html" not in response.headers.get("Content-Type", ""):
        raise ValueError("The supplied URL is not a web article.")

    if len(response.content) > 3_000_000:
        raise ValueError("The article page is too large to process.")

    soup = BeautifulSoup(response.text, "html.parser")

    title = ""
    for selector in [
        ("meta", {"property": "og:title"}),
        ("meta", {"name": "twitter:title"}),
    ]:
        node = soup.find(*selector)
        if node and node.get("content"):
            title = node["content"].strip()
            break

    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    if not title:
        title = urlparse(response.url).path.strip("/").replace("-", " ")

    return normalize_text(title)[:1000], response.url


# ============================================================
# LIVE SEARCH
# ============================================================

@lru_cache(maxsize=128)
def gdelt_search(query):
    encoded = quote(query)
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={encoded}&mode=artlist&maxrecords=8&timespan=7d&sort=datedesc&format=json"
    )
    response = requests.get(
        url,
        timeout=(4, 10),
        headers={"User-Agent": "EvidenceCompass/1.0"},
    )
    response.raise_for_status()
    data = response.json()

    results = []
    for article in data.get("articles", [])[:8]:
        article_url = article.get("url") or "#"
        title = normalize_text(article.get("title") or "Untitled article")
        domain = normalize_text(article.get("domain") or domain_from_url(article_url) or "Unknown source")
        results.append({
            "name": domain,
            "type": "Recent news coverage",
            "status": "related",
            "description": title,
            "date": article.get("seendate", ""),
            "url": article_url,
        })
    return results


def google_factcheck_search(query):
    """Optional Google Fact Check Tools lookup. Requires GOOGLE_FACTCHECK_API_KEY."""
    api_key = os.environ.get("GOOGLE_FACTCHECK_API_KEY")
    if not api_key:
        return []

    response = requests.get(
        "https://factchecktools.googleapis.com/v1alpha1/claims:search",
        params={
            "query": query,
            "languageCode": "en",
            "pageSize": 8,
            "key": api_key,
        },
        timeout=(4, 10),
    )
    response.raise_for_status()

    results = []
    for claim in response.json().get("claims", []):
        for review in claim.get("claimReview", [])[:2]:
            publisher = review.get("publisher", {}) or {}
            rating = normalize_text(review.get("textualRating", ""))
            rating_lower = rating.lower()
            if any(term in rating_lower for term in ["false", "misleading", "incorrect", "fake", "pants on fire", "mostly false"]):
                status = "conflict"
            elif any(term in rating_lower for term in ["true", "correct", "accurate", "mostly true"]):
                status = "related"
            else:
                status = "related"

            results.append({
                "name": publisher.get("name") or publisher.get("site") or "Fact-check publisher",
                "type": "Fact-check review",
                "status": status,
                "description": review.get("title") or claim.get("text") or "Fact-check review",
                "rating": rating,
                "date": review.get("reviewDate") or claim.get("claimDate") or "",
                "url": review.get("url") or "#",
            })

    return results[:8]


def google_news_search(query):
    url = (
        "https://news.google.com/rss/search"
        f"?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )
    response = requests.get(
        url,
        timeout=(4, 10),
        headers={"User-Agent": "Mozilla/5.0 EvidenceCompass/1.0"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)

    results = []
    for item in root.findall(".//item")[:8]:
        title = item.findtext("title") or "Untitled article"
        link = item.findtext("link") or "#"
        source = item.find("source")
        publisher = source.text.strip() if source is not None and source.text else domain_from_url(link)
        results.append({
            "name": publisher or "Unknown publisher",
            "type": "Recent news coverage",
            "status": "related",
            "description": normalize_text(title),
            "date": item.findtext("pubDate") or "",
            "url": link,
        })
    return results


def search_live_news(claim):
    query = clean_query(claim)
    searches = [query]

    # A second query is intentionally framed around verification/debunking so
    # that fact-check coverage is not treated the same as ordinary repetition.
    if len(query) > 20:
        searches.append(f"{query} fact check")

    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=len(searches) * 2) as pool:
        futures = []
        for q in searches:
            futures.append(pool.submit(gdelt_search, q))
            futures.append(pool.submit(google_news_search, q))
            if os.environ.get("GOOGLE_FACTCHECK_API_KEY"):
                futures.append(pool.submit(google_factcheck_search, q))

        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                errors.append(str(exc))

    # De-duplicate by canonical URL/title and keep the first 12 useful results.
    seen = set()
    unique = []
    for source in results:
        key = (source.get("url") or "", source.get("description") or "")
        if key in seen or not source.get("description"):
            continue
        seen.add(key)
        unique.append(source)

    if not unique and errors:
        logger.warning("News search unavailable: %s", errors[:2])

    return unique[:12]


# ============================================================
# ANALYSIS
# ============================================================

def analyze_sources(claim, sources):
    for source in sources:
        comparison = compare_claim_with_headline(claim, source.get("description", ""))
        source["comparison"] = comparison
        domain = domain_from_url(source.get("url", "")) or source.get("name", "")
        quality, quality_label = source_quality(domain)
        if source.get("type") == "Fact-check review":
            quality = max(quality, 0.85)
            quality_label = "Published fact-check review"
        source["quality"] = round(quality * 100)
        source["quality_label"] = quality_label

        if conflict_signal(source.get("description", "")):
            source["status"] = "conflict"
        elif comparison["relation"] in {"related", "weakly_related"}:
            source["status"] = "related"
        else:
            source["status"] = "insufficient"

    related = [s for s in sources if s["status"] == "related"]
    conflicts = [s for s in sources if s["status"] == "conflict"]
    strong_related = [
        s for s in related
        if s["comparison"]["relation"] == "related"
    ]

    # A user-supplied article from a recognized authoritative domain is
    # authoritative evidence even if the article headline itself has little
    # wording overlap with the extracted claim.
    authoritative = [
        s for s in sources
        if s.get("is_primary") and source_quality(domain_from_url(s.get("url", "")))[0] >= 1.0
    ]

    # Also include authoritative sources discovered during news/fact-check search.
    for s in sources:
        if s in authoritative:
            continue
        if s.get("quality", 0) >= 95:
            authoritative.append(s)

    unique_publishers = {
        (s.get("name") or "").strip().lower()
        for s in sources
        if s.get("name")
    }

    # This is a confidence/verification score, not a mathematical probability
    # that a claim is true. Coverage alone cannot establish truth.
    coverage_points = min(len(related) * 3, 18)
    diversity_points = min(len(unique_publishers) * 3, 15)
    headline_points = min(len(strong_related) * 3, 18)
    authority_points = min(len(authoritative) * 12, 24)
    conflict_penalty = min(len(conflicts) * 15, 30)

    if not sources:
        score = 50
        result = "NEEDS VERIFICATION"
        status = "verification"
    else:
        score = 50 + coverage_points + diversity_points + headline_points + authority_points - conflict_penalty

        # Never allow coverage/headline repetition alone to produce 100%.
        # A 100% result requires authoritative evidence.
        if not authoritative:
            score = min(score, 85)

        score = max(0, min(100, score))

        if authoritative and strong_related and not conflicts:
            result = "LIKELY RELIABLE"
            status = "reliable"
        elif conflicts and not authoritative:
            result = "QUESTIONABLE"
            status = "questionable"
        else:
            result = "NEEDS VERIFICATION"
            status = "verification"

    reasons = []
    if sources:
        reasons.append(f"{len(sources)} recent article(s) were found for the claim.")
        reasons.append(f"{len(unique_publishers)} distinct publisher/domain(s) were identified.")
        if strong_related:
            reasons.append(f"{len(strong_related)} headline(s) have substantial wording overlap with the claim.")
        if conflicts:
            reasons.append(f"{len(conflicts)} result(s) contain conflict or fact-check language.")
        if authoritative:
            reasons.append(f"{len(authoritative)} authoritative/primary source(s) were identified.")
        else:
            reasons.append("No authoritative original source was automatically confirmed.")
        reasons.append("Related news coverage does not by itself prove that a claim is true.")
    else:
        reasons = [
            "No recent matching news coverage was found.",
            "The claim requires additional verification.",
            "Check an authoritative or primary source before sharing.",
        ]

    breakdown = [
        {"label": "Related news coverage", "value": coverage_points},
        {"label": "Publisher diversity", "value": diversity_points},
        {"label": "Headline comparison", "value": headline_points},
        {"label": "Authoritative evidence", "value": authority_points},
        {"label": "Conflicting evidence", "value": -conflict_penalty},
    ]

    original = authoritative[0] if authoritative else None

    return {
        "score": score,
        "result": result,
        "status": status,
        "supporting_sources": len(related),
        "conflicting_sources": len(conflicts),
        "original_source": bool(original),
        "authoritative_source_confirmed": bool(original),
        "original_source_name": original.get("name") if original else "Not automatically identified",
        "original_source_url": original.get("url") if original else None,
        "reasons": reasons,
        "score_breakdown": breakdown,
        "sources": sources,
    }


# ============================================================
# ROUTES
# ============================================================

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    return response


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "EvidenceCompass"})


@app.route("/check", methods=["POST"])
def check_credibility():
    if rate_limited():
        return jsonify({"error": "Too many checks. Please wait a minute and try again."}), 429

    payload = request.get_json(silent=True) or {}
    claim = normalize_text(payload.get("claim", ""))
    source_url = normalize_text(payload.get("source_url", ""))

    if not claim:
        return jsonify({"error": "Please enter a news claim or article text."}), 400

    if len(claim) < 12:
        return jsonify({"error": "Please enter a more complete claim (at least 12 characters)."}), 400

    if len(claim) > 5000:
        return jsonify({"error": "Please keep the claim under 5,000 characters."}), 400

    original_input = claim
    article_url = None
    submitted_source_title = None

    # Backward-compatible mode: a URL pasted into the main claim box is still
    # treated as the article URL.
    if re.match(r"^https?://", claim, re.I) and not source_url:
        source_url = claim

    # Optional source URL mode: users can paste the article text/claim and
    # separately provide the original source. We validate and read the source
    # page, but we do NOT replace the user's claim text with the page title.
    if source_url:
        if not re.match(r"^https?://", source_url, re.I):
            return jsonify({"error": "Source URL must start with http:// or https://."}), 400
        try:
            submitted_source_title, article_url = extract_article_text(source_url)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except requests.RequestException:
            return jsonify({"error": "We could not read that source URL. Check the URL and try again."}), 400

        # If the main field itself was a URL, use the fetched title as the
        # claim being checked; otherwise preserve the user's pasted claim.
        if re.match(r"^https?://", original_input, re.I):
            claim = submitted_source_title or claim

    started = time.time()
    sources = search_live_news(claim)

    # The user-supplied source is explicit provenance. This is the key
    # difference between pasting article text alone and pasting article text
    # plus its original URL. Recognized authoritative domains (NASA, PIB,
    # WHO, government domains, etc.) are therefore visible to the analyzer.
    if article_url:
        submitted_domain = domain_from_url(article_url)
        quality, quality_label = source_quality(submitted_domain)
        sources.insert(0, {
            "name": submitted_domain or "Submitted article",
            "type": "User-supplied primary source",
            "status": "related",
            "description": submitted_source_title or claim[:1000],
            "date": "",
            "url": article_url,
            "is_primary": True,
            "quality": round(quality * 100),
            "quality_label": quality_label,
        })

    analysis = analyze_sources(claim, sources)

    result = {
        "claim": claim,
        "original_input": original_input,
        "article_url": article_url,
        "source_url": article_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        **analysis,
    }

    # Save a lightweight anonymous history record. The signed session cookie
    # contains only the visitor id, not the claim itself.
    try:
        record = Check(
            visitor_id=get_visitor_id(),
            claim=claim[:5000],
            score=analysis["score"],
            result=analysis["result"],
            status=analysis["status"],
        )
        db.session.add(record)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Could not save history")

    logger.info("Verification completed in %.2fs", time.time() - started)
    return jsonify(result)


@app.route("/api/share", methods=["POST"])
def create_share_result():
    """Create a public, expiring snapshot of a verification result."""
    if rate_limited():
        return jsonify({"error": "Too many requests. Please wait a minute and try again."}), 429

    payload = request.get_json(silent=True) or {}
    result = payload.get("result")
    if not isinstance(result, dict):
        return jsonify({"error": "A verification result is required."}), 400

    # Keep the public snapshot limited to fields that are already displayed on
    # the result page. Do not store the user's browser/session identifier.
    allowed = {
        "claim", "score", "result", "status", "supporting_sources",
        "conflicting_sources", "original_source", "original_source_name",
        "original_source_url", "reasons", "score_breakdown", "sources",
        "article_url",
    }
    snapshot = {k: result.get(k) for k in allowed if k in result}
    if not snapshot.get("claim"):
        return jsonify({"error": "The result is missing its claim."}), 400

    # Remove stale links opportunistically. Public share links expire after 30 days.
    now = datetime.now(timezone.utc)
    try:
        ShareResult.query.filter(ShareResult.expires_at < now).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()

    token = secrets.token_urlsafe(18)
    expires_at = now + timedelta(days=30)
    row = ShareResult(
        token=token,
        payload=json.dumps(snapshot, ensure_ascii=False),
        created_at=now,
        expires_at=expires_at,
    )
    db.session.add(row)
    db.session.commit()

    public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    share_base = public_base or request.host_url.rstrip("/")

    return jsonify({
        "url": share_base + "/share/" + token,
        "expires_at": expires_at.isoformat(),
    })


@app.route("/share/<token>", methods=["GET"])
def view_shared_result(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,32}", token or ""):
        return render_template("share.html", data=None), 404

    row = ShareResult.query.filter_by(token=token).first()
    if not row:
        return render_template("share.html", data=None), 404

    now = datetime.now(timezone.utc)
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        db.session.delete(row)
        db.session.commit()
        return render_template("share.html", data=None), 410

    try:
        data = json.loads(row.payload)
    except (TypeError, json.JSONDecodeError):
        return render_template("share.html", data=None), 500

    return render_template("share.html", data=data, expires_at=expires_at.isoformat())


@app.route("/api/history", methods=["GET"])
def history():
    visitor_id = session.get("visitor_id")
    if not visitor_id:
        return jsonify({"items": []})

    rows = (
        Check.query
        .filter_by(visitor_id=visitor_id)
        .order_by(Check.created_at.desc())
        .limit(20)
        .all()
    )

    return jsonify({
        "items": [
            {
                "id": row.id,
                "claim": row.claim,
                "score": row.score,
                "result": row.result,
                "status": row.status,
                "time": row.created_at.replace(tzinfo=timezone.utc).isoformat(),
            }
            for row in rows
        ]
    })


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    visitor_id = session.get("visitor_id")
    if visitor_id:
        Check.query.filter_by(visitor_id=visitor_id).delete()
        db.session.commit()
    return jsonify({"ok": True})


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Request is too large."}), 413


@app.errorhandler(500)
def internal_error(_error):
    db.session.rollback()
    return jsonify({"error": "EvidenceCompass encountered an internal error. Please try again."}), 500


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
