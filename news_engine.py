"""
EvidenceCompass news-search and credibility engine.
Streamlit-safe version: no Flask, server, database, or browser session dependencies.
"""
import ipaddress
import logging
import os
import re
import socket
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("evidencecompass")

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
    "false", "fake", "falsely", "debunked", "debunk", "misleading",
    "misinformation", "disinformation", "denies", "denied", "deny",
    "no evidence", "untrue", "incorrect", "hoax", "scam", "fact check",
    "fact-check", "clarification",
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

def normalize_text(text):
    return re.sub(r"\s+", " ", text or "").strip()

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
    relation = "related" if percentage >= 50 else ("weakly_related" if percentage >= 25 else "insufficient")
    return {"relation": relation, "matched_words": len(common), "match_percentage": percentage}

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
    unique = list(dict.fromkeys(words))
    return " ".join(unique[:10]) or claim[:150]

def is_safe_public_url(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname
        if host.lower() in {"localhost", "localhost.localdomain"}:
            return False
        for info in socket.getaddrinfo(host, None):
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
        url, timeout=(4, 8), allow_redirects=True,
        headers={"User-Agent": "EvidenceCompass/1.0"},
    )
    response.raise_for_status()
    if not is_safe_public_url(response.url):
        raise ValueError("That article redirects to a URL that cannot be fetched safely.")
    if "text/html" not in response.headers.get("Content-Type", ""):
        raise ValueError("The supplied URL is not a web article.")
    if len(response.content) > 3_000_000:
        raise ValueError("The article page is too large to process.")
    soup = BeautifulSoup(response.text, "html.parser")
    title = ""
    for selector in [("meta", {"property": "og:title"}), ("meta", {"name": "twitter:title"})]:
        node = soup.find(*selector)
        if node and node.get("content"):
            title = node["content"].strip()
            break
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    if not title:
        title = urlparse(response.url).path.strip("/").replace("-", " ")
    return normalize_text(title)[:1000], response.url

@lru_cache(maxsize=128)
def gdelt_search(query):
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc"
        f"?query={quote(query)}&mode=artlist&maxrecords=8&timespan=7d&sort=datedesc&format=json"
    )
    response = requests.get(url, timeout=(4, 10), headers={"User-Agent": "EvidenceCompass/1.0"})
    response.raise_for_status()
    results = []
    for article in response.json().get("articles", [])[:8]:
        article_url = article.get("url") or "#"
        title = normalize_text(article.get("title") or "Untitled article")
        domain = normalize_text(article.get("domain") or domain_from_url(article_url) or "Unknown source")
        results.append({
            "name": domain, "type": "Recent news coverage", "status": "related",
            "description": title, "date": article.get("seendate", ""), "url": article_url,
        })
    return results

def google_factcheck_search(query):
    api_key = os.environ.get("GOOGLE_FACTCHECK_API_KEY")
    if not api_key:
        return []
    response = requests.get(
        "https://factchecktools.googleapis.com/v1alpha1/claims:search",
        params={"query": query, "languageCode": "en", "pageSize": 8, "key": api_key},
        timeout=(4, 10),
    )
    response.raise_for_status()
    results = []
    for claim in response.json().get("claims", []):
        for review in claim.get("claimReview", [])[:2]:
            publisher = review.get("publisher", {}) or {}
            rating = normalize_text(review.get("textualRating", ""))
            low = rating.lower()
            status = "conflict" if any(t in low for t in [
                "false", "misleading", "incorrect", "fake", "pants on fire", "mostly false"
            ]) else "related"
            results.append({
                "name": publisher.get("name") or publisher.get("site") or "Fact-check publisher",
                "type": "Fact-check review", "status": status,
                "description": review.get("title") or claim.get("text") or "Fact-check review",
                "rating": rating, "date": review.get("reviewDate") or claim.get("claimDate") or "",
                "url": review.get("url") or "#",
            })
    return results[:8]

def google_news_search(query):
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    response = requests.get(url, timeout=(4, 10), headers={"User-Agent": "Mozilla/5.0 EvidenceCompass/1.0"})
    response.raise_for_status()
    root = ET.fromstring(response.content)
    results = []
    for item in root.findall(".//item")[:8]:
        title = item.findtext("title") or "Untitled article"
        link = item.findtext("link") or "#"
        source = item.find("source")
        publisher = source.text.strip() if source is not None and source.text else domain_from_url(link)
        results.append({
            "name": publisher or "Unknown publisher", "type": "Recent news coverage",
            "status": "related", "description": normalize_text(title),
            "date": item.findtext("pubDate") or "", "url": link,
        })
    return results

def search_live_news(claim):
    query = clean_query(claim)
    searches = [query]
    if len(query) > 20:
        searches.append(f"{query} fact check")
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=len(searches) * 3) as pool:
        futures = []
        for q in searches:
            futures += [pool.submit(gdelt_search, q), pool.submit(google_news_search, q)]
            if os.environ.get("GOOGLE_FACTCHECK_API_KEY"):
                futures.append(pool.submit(google_factcheck_search, q))
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                errors.append(str(exc))
    seen, unique = set(), []
    for source in results:
        key = (source.get("url") or "", source.get("description") or "")
        if key in seen or not source.get("description"):
            continue
        seen.add(key)
        unique.append(source)
    if not unique and errors:
        logger.warning("News search unavailable: %s", errors[:2])
    return unique[:12]

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
    strong_related = [s for s in related if s["comparison"]["relation"] == "related"]
    authoritative = [
        s for s in sources
        if s.get("is_primary") and source_quality(domain_from_url(s.get("url", "")))[0] >= 1.0
    ]
    for s in sources:
        if s in authoritative:
            continue
        if s.get("quality", 0) >= 95:
            authoritative.append(s)
    unique_publishers = {(s.get("name") or "").strip().lower() for s in sources if s.get("name")}

    coverage_points = min(len(related) * 3, 18)
    diversity_points = min(len(unique_publishers) * 3, 15)
    headline_points = min(len(strong_related) * 3, 18)
    authority_points = min(len(authoritative) * 12, 24)
    conflict_penalty = min(len(conflicts) * 15, 30)

    if not sources:
        score, result, status = 50, "NEEDS VERIFICATION", "verification"
    else:
        score = 50 + coverage_points + diversity_points + headline_points + authority_points - conflict_penalty
        if not authoritative:
            score = min(score, 85)
        score = max(0, min(100, score))
        if authoritative and strong_related and not conflicts:
            result, status = "LIKELY RELIABLE", "reliable"
        elif conflicts and not authoritative:
            result, status = "QUESTIONABLE", "questionable"
        else:
            result, status = "NEEDS VERIFICATION", "verification"

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
        reasons = ["No recent matching news coverage was found.",
                   "The claim requires additional verification.",
                   "Check an authoritative or primary source before sharing."]

    return {
        "score": score, "result": result, "status": status,
        "supporting_sources": len(related), "conflicting_sources": len(conflicts),
        "original_source": bool(authoritative),
        "authoritative_source_confirmed": bool(authoritative),
        "original_source_name": authoritative[0].get("name") if authoritative else "Not automatically identified",
        "original_source_url": authoritative[0].get("url") if authoritative else None,
        "reasons": reasons,
        "score_breakdown": [
            {"label": "Related news coverage", "value": coverage_points},
            {"label": "Publisher diversity", "value": diversity_points},
            {"label": "Headline comparison", "value": headline_points},
            {"label": "Authoritative evidence", "value": authority_points},
            {"label": "Conflicting evidence", "value": -conflict_penalty},
        ],
        "sources": sources,
    }

def check_claim(claim, source_url=""):
    claim = normalize_text(claim)
    source_url = normalize_text(source_url)
    if not claim:
        raise ValueError("Please enter a news claim or article text.")
    if len(claim) < 12:
        raise ValueError("Please enter a more complete claim (at least 12 characters).")
    if len(claim) > 5000:
        raise ValueError("Please keep the claim under 5,000 characters.")

    original_input = claim
    article_url = None
    submitted_source_title = None
    if re.match(r"^https?://", claim, re.I) and not source_url:
        source_url = claim

    if source_url:
        if not re.match(r"^https?://", source_url, re.I):
            raise ValueError("Source URL must start with http:// or https://.")
        try:
            submitted_source_title, article_url = extract_article_text(source_url)
        except ValueError:
            raise
        except requests.RequestException:
            raise ValueError("We could not read that source URL. Check the URL and try again.")
        if re.match(r"^https?://", original_input, re.I):
            claim = submitted_source_title or claim

    sources = search_live_news(claim)
    if article_url:
        submitted_domain = domain_from_url(article_url)
        quality, quality_label = source_quality(submitted_domain)
        sources.insert(0, {
            "name": submitted_domain or "Submitted article",
            "type": "User-supplied primary source", "status": "related",
            "description": submitted_source_title or claim[:1000], "date": "",
            "url": article_url, "is_primary": True,
            "quality": round(quality * 100), "quality_label": quality_label,
        })

    analysis = analyze_sources(claim, sources)
    return {
        "claim": claim, "original_input": original_input,
        "article_url": article_url, "source_url": article_url, **analysis,
    }
