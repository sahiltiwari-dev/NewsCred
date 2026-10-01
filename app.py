import os
from datetime import datetime, timezone
import streamlit as st

from news_engine import check_claim

st.set_page_config(
    page_title="EvidenceCompass — News Credibility",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Streamlit Cloud secrets are exposed as environment variables here.
# GOOGLE_FACTCHECK_API_KEY is optional.
if "GOOGLE_FACTCHECK_API_KEY" not in os.environ:
    try:
        if "GOOGLE_FACTCHECK_API_KEY" in st.secrets:
            os.environ["GOOGLE_FACTCHECK_API_KEY"] = st.secrets["GOOGLE_FACTCHECK_API_KEY"]
    except Exception:
        pass

st.markdown("""
<style>
.main-title {font-size: 2.7rem; font-weight: 800; margin-bottom: 0;}
.subtitle {font-size: 1.05rem; opacity: .75; margin-bottom: 1.5rem;}
.score-card {padding: 1.2rem; border: 1px solid rgba(128,128,128,.25);
             border-radius: 16px; text-align:center; margin-bottom:1rem;}
.source-card {padding: 1rem; border: 1px solid rgba(128,128,128,.2);
              border-radius: 12px; margin-bottom: .7rem;}
.small {font-size:.85rem; opacity:.7;}
</style>
""", unsafe_allow_html=True)

if "history" not in st.session_state:
    st.session_state.history = []
if "result" not in st.session_state:
    st.session_state.result = None

with st.sidebar:
    st.markdown("## 🧭 EvidenceCompass")
    st.caption("News credibility verification")
    st.divider()
    st.markdown("### How it works")
    st.write("1. Search recent news coverage")
    st.write("2. Compare your claim with headlines")
    st.write("3. Identify publisher quality and conflict signals")
    st.write("4. Show an explainable verification-confidence score")
    st.divider()
    st.info("A score is not a mathematical probability that a claim is true. Always inspect the cited sources.")
    if st.button("🗑️ Clear local history", use_container_width=True):
        st.session_state.history = []
        st.session_state.result = None
        st.rerun()

st.markdown('<div class="main-title">EvidenceCompass</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Check a news claim against recent coverage and published fact-check evidence.</div>', unsafe_allow_html=True)

with st.form("check_form"):
    claim = st.text_area(
        "News claim",
        placeholder="Example: The government announced a new policy today...",
        height=130,
    )
    source_url = st.text_input(
        "Optional original article URL",
        placeholder="https://example.com/news/article",
    )
    submitted = st.form_submit_button("🔎 Check credibility", type="primary", use_container_width=True)

if submitted:
    with st.spinner("Searching recent coverage and analysing sources..."):
        try:
            result = check_claim(claim, source_url)
            st.session_state.result = result
            st.session_state.history.insert(0, {
                "claim": result["claim"],
                "score": result["score"],
                "result": result["result"],
                "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            })
            st.session_state.history = st.session_state.history[:20]
        except Exception as exc:
            st.error(str(exc))

result = st.session_state.result
if result:
    score = int(result["score"])
    st.divider()
    left, mid, right = st.columns([1, 1.5, 1])
    with left:
        st.metric("Verification confidence", f"{score}/100")
        st.progress(score / 100)
    with mid:
        st.subheader(result["result"])
        st.caption(result["claim"])
    with right:
        st.metric("Supporting sources", result["supporting_sources"])
        st.metric("Conflicting sources", result["conflicting_sources"])

    if result.get("authoritative_source_confirmed"):
        st.success(f"Authoritative/primary evidence identified: {result.get('original_source_name', 'Source')}")
    else:
        st.warning("No authoritative original source was automatically confirmed.")

    st.subheader("Why this score?")
    for reason in result["reasons"]:
        st.write("• " + reason)

    st.subheader("Score breakdown")
    breakdown = result["score_breakdown"]
    cols = st.columns(len(breakdown))
    for col, item in zip(cols, breakdown):
        with col:
            value = item["value"]
            st.metric(item["label"], f"{value:+d}" if value < 0 else f"+{value}")

    st.subheader("Evidence")
    if not result["sources"]:
        st.info("No recent matching coverage was found.")
    else:
        for source in result["sources"]:
            status = source.get("status", "insufficient")
            icon = "🟢" if status == "related" else ("🔴" if status == "conflict" else "⚪")
            title = source.get("description", "Untitled article")
            publisher = source.get("name", "Unknown publisher")
            quality = source.get("quality", 0)
            with st.container(border=True):
                st.markdown(f"**{icon} {title}**")
                st.caption(f"{publisher} · {source.get('type','Source')} · source quality {quality}/100")
                comparison = source.get("comparison", {})
                if comparison:
                    st.caption(
                        f"Headline match: {comparison.get('match_percentage', 0)}% "
                        f"({comparison.get('relation', 'insufficient')})"
                    )
                if source.get("rating"):
                    st.caption(f"Fact-check rating: {source['rating']}")
                url = source.get("url")
                if url and url != "#":
                    st.link_button("Open source", url)

    st.caption("Checked using live public news/fact-check feeds. External services may occasionally be unavailable.")

with st.expander("Recent checks"):
    if st.session_state.history:
        for item in st.session_state.history:
            st.write(f"**{item['score']}/100 — {item['result']}** · {item['time']}")
            st.caption(item["claim"])
    else:
        st.caption("No checks in this browser session yet.")

st.divider()
st.caption("EvidenceCompass • Streamlit deployment build")
