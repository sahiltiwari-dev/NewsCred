const API_URL = "http://127.0.0.1:8000/api/check";

const form = document.getElementById("claimForm");
const claimInput = document.getElementById("claim");
const urlInput = document.getElementById("url");
const result = document.getElementById("result");
const loading = document.getElementById("loading");
const errorBox = document.getElementById("error");

document.querySelectorAll(".sample").forEach(button => {
  button.addEventListener("click", () => {
    claimInput.value = button.dataset.claim;
    claimInput.focus();
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  hide(errorBox);
  hide(result);
  show(loading);

  try {
    const response = await fetch(API_URL, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        claim: claimInput.value.trim(),
        url: urlInput.value.trim() || null
      })
    });

    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The API returned an error.");

    renderResult(payload);
  } catch (error) {
    errorBox.textContent = `${error.message} Make sure the FastAPI server is running on port 8000.`;
    show(errorBox);
  } finally {
    hide(loading);
  }
});

function renderResult(data) {
  document.getElementById("resultTitle").textContent = data.support_level;
  document.getElementById("matchedClaim").textContent = `Matched record: ${data.record_title}`;
  document.getElementById("score").textContent = data.score;
  document.getElementById("original").textContent = data.original_source_available ? "Available" : "Missing";
  document.getElementById("originalDetail").textContent = data.original_source_clue;
  document.getElementById("context").textContent = data.context_match ? "Matched" : "Concern";
  document.getElementById("contextDetail").textContent = data.context_note;

  const pill = document.getElementById("levelPill");
  pill.textContent = data.support_level;
  pill.className = "level-pill " + levelClass(data.support_level);

  const reasons = document.getElementById("reasons");
  reasons.innerHTML = "";
  data.reasons.forEach(reason => {
    const li = document.createElement("li");
    li.textContent = reason;
    reasons.appendChild(li);
  });

  const bars = document.getElementById("signalBars");
  bars.innerHTML = "";
  data.signals.forEach(signal => {
    const wrapper = document.createElement("div");
    wrapper.className = "signal";
    const sign = signal.points > 0 ? "+" : "";
    const width = Math.min(Math.abs(signal.points) / 2 * 100, 100);
    wrapper.innerHTML = `
      <div class="signal-top">
        <span>${escapeHtml(signal.label)}</span>
        <strong>${sign}${signal.points}</strong>
      </div>
      <div class="bar"><div class="fill ${signal.points >= 0 ? "positive" : "negative"}" style="width:${width}%"></div></div>
    `;
    bars.appendChild(wrapper);
  });

  renderSources("supportingSources", data.supporting_sources, "No supporting sources in this record.");
  renderSources("conflictingSources", data.conflicting_sources, "No conflicting sources in this record.");
  document.getElementById("explanation").textContent = data.explanation;
  show(result);
}

function renderSources(id, sources, emptyText) {
  const box = document.getElementById(id);
  box.innerHTML = "";
  if (!sources.length) {
    box.textContent = emptyText;
    box.className = "muted";
    return;
  }
  box.className = "";
  sources.forEach(source => {
    const div = document.createElement("div");
    div.className = "source";
    div.innerHTML = `
      <strong>${escapeHtml(source.name)}</strong>
      <small>${escapeHtml(source.type)} · ${escapeHtml(source.date)} · ${escapeHtml(source.note)}</small>
    `;
    box.appendChild(div);
  });
}

function levelClass(level) {
  if (level === "Higher support") return "level-higher";
  if (level === "Mixed") return "level-mixed";
  return "level-low";
}

function show(el) { el.classList.remove("hidden"); }
function hide(el) { el.classList.add("hidden"); }
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;"
  }[c]));
}
