(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  const isHttp = (u) => typeof u === 'string' && /^https?:\/\//i.test(u);

  // ---------- Theme toggle (runs on every page) ----------
  const themeBtn = $('#theme-toggle');
  if (themeBtn) {
    const sync = () => themeBtn.setAttribute('aria-pressed', String(document.documentElement.dataset.theme === 'dark'));
    sync();
    themeBtn.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem('ec-theme', next); } catch (e) { /* storage may be blocked */ }
      sync();
    });
  }

  const form = $('#check-form');
  if (!form) return; // Only the home page has the checker.

  // ---------- Elements ----------
  const claimEl = $('#claim');
  const sourceEl = $('#source_url');
  const countEl = $('#count');
  const submitBtn = $('#submit');
  const errorEl = $('#form-error');
  const meter = $('#meter');
  const needle = $('#needle');
  const dialValue = $('#dial-value');
  const scoreNum = $('#score-num');
  const verdictEl = $('#verdict');
  const verdictNote = $('#verdict-note');
  const stageEl = $('#stage');
  const report = $('#report');

  let lastResult = null;
  let filter = 'all';
  let stageTimer = null;
  let countTimer = null;

  const VERDICTS = {
    reliable: 'Independent coverage and at least one authoritative source line up with this claim.',
    verification: 'Some evidence was found, but not enough to call this reliable. Read the sources before you share it.',
    questionable: 'Results include fact-check or dispute language, and no authoritative source backs the claim.',
  };
  const STAGES = [
    'Searching recent news...',
    'Looking for fact-checks...',
    'Comparing headlines with your claim...',
    'Weighing the sources...',
  ];
  const STATUS_LABEL = {
    related: 'Matches the claim',
    conflict: 'Disputes or fact-checks',
    insufficient: 'Weak match',
  };

  const sentence = (s) => {
    const t = String(s || '').toLowerCase();
    return t.charAt(0).toUpperCase() + t.slice(1);
  };

  // ---------- Form basics ----------
  function updateCount() { countEl.textContent = `${claimEl.value.length} / 5000`; }
  claimEl.addEventListener('input', updateCount);
  claimEl.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') form.requestSubmit();
  });

  document.querySelectorAll('[data-fill]').forEach((btn) => {
    btn.addEventListener('click', () => {
      claimEl.value = btn.dataset.fill;
      updateCount();
      claimEl.focus();
    });
  });

  $('#clear').addEventListener('click', () => {
    claimEl.value = '';
    sourceEl.value = '';
    updateCount();
    hideError();
    resetMeter();
    report.hidden = true;
    lastResult = null;
    claimEl.focus();
  });

  function showError(msg) { errorEl.textContent = msg; errorEl.hidden = false; }
  function hideError() { errorEl.hidden = true; errorEl.textContent = ''; }

  // ---------- Meter ----------
  function animateNumber(target) {
    clearInterval(countTimer);
    if (reduceMotion) { scoreNum.textContent = target; return; }
    const start = performance.now();
    const duration = 1200;
    const tick = (now) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      scoreNum.textContent = Math.round(target * eased);
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function setDial(score) {
    dialValue.style.strokeDashoffset = String(100 - score);
    needle.style.transform = `rotate(${-90 + score * 1.8}deg)`;
  }

  function resetMeter() {
    meter.dataset.state = 'idle';
    needle.style.transform = 'rotate(-90deg)';
    dialValue.style.strokeDashoffset = '100';
    scoreNum.textContent = '--';
    verdictEl.textContent = 'Waiting for a claim';
    verdictNote.textContent = 'Paste a claim on the left to see how well it is supported by recent coverage and sources.';
    $('#stat-support').textContent = '--';
    $('#stat-conflict').textContent = '--';
    $('#stat-original').textContent = '--';
    stageEl.hidden = true;
  }

  function startLoading() {
    meter.dataset.state = 'loading';
    needle.style.transform = 'rotate(-90deg)';
    dialValue.style.strokeDashoffset = '100';
    scoreNum.textContent = '...';
    verdictEl.textContent = 'Checking';
    verdictNote.textContent = 'This usually takes a few seconds.';
    stageEl.hidden = false;
    let i = 0;
    stageEl.textContent = STAGES[0];
    stageTimer = setInterval(() => {
      i = (i + 1) % STAGES.length;
      stageEl.textContent = STAGES[i];
    }, 1600);
  }

  function stopLoading() {
    clearInterval(stageTimer);
    stageEl.hidden = true;
  }

  function showResult(r) {
    const state = ['reliable', 'verification', 'questionable'].includes(r.status) ? r.status : 'verification';
    const score = Math.max(0, Math.min(100, Number(r.score) || 0));
    meter.dataset.state = state;
    verdictEl.textContent = sentence(r.result);
    verdictNote.textContent = VERDICTS[state];
    $('#stat-support').textContent = r.supporting_sources ?? 0;
    $('#stat-conflict').textContent = r.conflicting_sources ?? 0;
    $('#stat-original').textContent = r.original_source ? 'Found' : 'Not found';
    // Let the browser register the reset position first so the sweep animates.
    requestAnimationFrame(() => requestAnimationFrame(() => setDial(score)));
    animateNumber(score);
  }

  // ---------- Report ----------
  function formatDate(raw) {
    if (!raw) return '';
    let d;
    const g = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(raw);
    if (g) d = new Date(Date.UTC(+g[1], +g[2] - 1, +g[3], +g[4], +g[5], +g[6]));
    else d = new Date(raw);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function renderBreakdown(r) {
    const box = $('#breakdown');
    box.replaceChildren();

    const base = el('div', 'bd-row bd-base');
    base.append(el('span', 'bd-label', 'Starting point'), el('span', 'bd-track'), el('span', 'bd-val', '50'));
    box.append(base);

    (r.score_breakdown || []).forEach((item) => {
      const v = Number(item.value) || 0;
      const row = el('div', 'bd-row' + (v < 0 ? ' is-neg' : ''));
      const track = el('span', 'bd-track');
      const fill = el('span', 'bd-fill');
      fill.style.width = `${Math.min(100, (Math.abs(v) / 30) * 100)}%`;
      track.append(fill);
      row.append(el('span', 'bd-label', item.label), track, el('span', 'bd-val', (v > 0 ? '+' : '') + v));
      box.append(row);
    });

    const total = el('div', 'bd-row bd-total');
    total.append(el('span', 'bd-label', 'Verification score'), el('span', 'bd-track'), el('span', 'bd-val', String(r.score)));
    total.style.fontWeight = '800';
    box.append(total);

    $('#cap-note').hidden = !(r.score === 85 && !r.authoritative_source_confirmed);
  }

  function renderFilters(sources) {
    const box = $('#filters');
    box.replaceChildren();
    const counts = {
      all: sources.length,
      related: sources.filter((s) => s.status === 'related').length,
      conflict: sources.filter((s) => s.status === 'conflict').length,
      insufficient: sources.filter((s) => s.status === 'insufficient').length,
    };
    const labels = { all: 'All', related: 'Supporting', conflict: 'Disputing', insufficient: 'Weak match' };
    Object.keys(labels).forEach((key) => {
      if (key !== 'all' && counts[key] === 0) return;
      const b = el('button', null, `${labels[key]} (${counts[key]})`);
      b.type = 'button';
      b.setAttribute('aria-pressed', String(filter === key));
      b.addEventListener('click', () => { filter = key; renderFilters(sources); renderSources(sources); });
      box.append(b);
    });
  }

  function renderSources(sources) {
    const list = $('#sources');
    list.replaceChildren();
    const shown = sources.filter((s) => filter === 'all' || s.status === filter);
    $('#sources-empty').hidden = sources.length !== 0;

    shown.forEach((s) => {
      const status = STATUS_LABEL[s.status] ? s.status : 'insufficient';
      const li = el('li', `source is-${status}`);

      const top = el('div', 'source-top');
      top.append(el('strong', 'source-name', s.name || 'Unknown source'), el('span', 'tag', s.type || 'Source'));
      const date = formatDate(s.date);
      if (date) top.append(el('span', 'source-date', date));
      li.append(top);

      const title = el('p', 'source-title');
      if (isHttp(s.url)) {
        const a = el('a', null, s.description || 'Untitled');
        a.href = s.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer nofollow';
        title.append(a);
      } else {
        title.textContent = s.description || 'Untitled';
      }
      li.append(title);

      const meta = el('p', 'source-meta');
      meta.append(el('span', null, STATUS_LABEL[status]));
      if (s.rating) meta.append(el('span', 'rated', `Rated: ${s.rating}`));
      if (s.comparison && typeof s.comparison.match_percentage === 'number') {
        const m = el('span', 'match');
        const bar = el('i');
        const inner = el('b');
        inner.style.width = `${Math.min(100, s.comparison.match_percentage)}%`;
        bar.append(inner);
        m.append(document.createTextNode(`Wording match ${s.comparison.match_percentage}%`), bar);
        meta.append(m);
      }
      if (s.quality_label) meta.append(el('span', null, `${s.quality_label} (${s.quality})`));
      li.append(meta);

      list.append(li);
    });
  }

  function renderReport(r) {
    report.hidden = false;
    $('#claim-echo').textContent = r.claim;

    const link = $('#claim-link');
    if (isHttp(r.article_url)) {
      link.href = r.article_url;
      link.textContent = `Source: ${r.article_url}`;
      link.hidden = false;
    } else {
      link.hidden = true;
    }

    $('#reasons').replaceChildren(...(r.reasons || []).map((t) => el('li', null, t)));

    const orig = $('#original-line');
    orig.replaceChildren();
    if (r.original_source && isHttp(r.original_source_url)) {
      orig.append(document.createTextNode('Strongest source: '));
      const a = el('a', null, r.original_source_name || r.original_source_url);
      a.href = r.original_source_url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer nofollow';
      orig.append(a);
    } else {
      orig.textContent = 'No authoritative original source was confirmed automatically.';
    }

    renderBreakdown(r);
    filter = 'all';
    const sources = r.sources || [];
    renderFilters(sources);
    renderSources(sources);

    $('#share-out').hidden = true;
    $('#share-status').textContent = '';
  }

  // ---------- Submit ----------
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    hideError();

    const claim = claimEl.value.trim();
    if (claim.length < 12) {
      showError('Enter a more complete claim: at least 12 characters.');
      claimEl.focus();
      return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Checking...';
    form.setAttribute('aria-busy', 'true');
    report.hidden = true;
    startLoading();

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 45000);

    try {
      const res = await fetch('/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ claim, source_url: sourceEl.value.trim() }),
        signal: controller.signal,
      });
      let data = {};
      try { data = await res.json(); } catch (err) { /* non-JSON error page */ }
      if (!res.ok) throw new Error(data.error || 'Something went wrong. Try again in a moment.');

      lastResult = data;
      stopLoading();
      showResult(data);
      renderReport(data);

      const narrow = window.matchMedia('(max-width: 900px)').matches;
      const target = narrow ? meter : report;
      setTimeout(() => target.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' }), 250);
    } catch (err) {
      stopLoading();
      resetMeter();
      showError(err.name === 'AbortError'
        ? 'The check took too long. Try a shorter claim or try again.'
        : err.message);
    } finally {
      clearTimeout(timeout);
      submitBtn.disabled = false;
      submitBtn.textContent = 'Check claim';
      form.removeAttribute('aria-busy');
    }
  });

  // ---------- Share ----------
  const shareStatus = $('#share-status');
  const shareOut = $('#share-out');
  const shareUrl = $('#share-url');

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      return false;
    }
  }

  $('#share-btn').addEventListener('click', async () => {
    if (!lastResult) return;
    shareStatus.textContent = 'Creating link...';
    try {
      const res = await fetch('/api/share', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ result: lastResult }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Could not create a share link.');
      shareUrl.value = data.url;
      shareOut.hidden = false;
      shareUrl.focus();
      shareUrl.select();
      shareStatus.textContent = 'Link created. It expires in 30 days.';
    } catch (err) {
      shareStatus.textContent = err.message;
    }
  });

  $('#copy-link').addEventListener('click', async () => {
    const ok = await copyText(shareUrl.value);
    if (!ok) shareUrl.select();
    shareStatus.textContent = ok ? 'Link copied.' : 'Press Ctrl + C to copy the selected link.';
  });

  $('#copy-summary').addEventListener('click', async () => {
    if (!lastResult) return;
    const r = lastResult;
    const text = `EvidenceCompass check: "${r.claim.slice(0, 200)}"\n${sentence(r.result)} (${r.score}/100). ` +
      `${r.supporting_sources} supporting, ${r.conflicting_sources} disputing sources.`;
    const ok = await copyText(text);
    shareStatus.textContent = ok ? 'Summary copied.' : 'Your browser blocked copying. Select the text manually.';
  });

  // ---------- History ----------
  const dialog = $('#history-dialog');
  const historyList = $('#history-list');
  const historyEmpty = $('#history-empty');

  function relTime(iso) {
    const diff = (new Date(iso).getTime() - Date.now()) / 1000;
    const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
    const units = [['day', 86400], ['hour', 3600], ['minute', 60]];
    for (const [unit, secs] of units) {
      if (Math.abs(diff) >= secs) return rtf.format(Math.round(diff / secs), unit);
    }
    return 'just now';
  }

  async function loadHistory() {
    historyList.replaceChildren();
    try {
      const res = await fetch('/api/history');
      const data = await res.json();
      const items = data.items || [];
      historyEmpty.hidden = items.length > 0;
      $('#history-clear').hidden = items.length === 0;
      items.forEach((it) => {
        const li = el('li');
        const btn = el('button', 'history-item');
        btn.type = 'button';
        btn.dataset.status = it.status;
        const body = el('span');
        body.append(el('span', 'h-claim', it.claim), el('span', 'h-meta', `${sentence(it.result)} \u00b7 ${relTime(it.time)}`));
        btn.append(el('span', 'h-score', String(it.score)), body);
        btn.addEventListener('click', () => {
          claimEl.value = it.claim;
          updateCount();
          dialog.close();
          claimEl.focus();
        });
        li.append(btn);
        historyList.append(li);
      });
    } catch (e) {
      historyEmpty.textContent = 'History could not be loaded right now.';
      historyEmpty.hidden = false;
    }
  }

  $('#history-open').addEventListener('click', () => { dialog.showModal(); loadHistory(); });
  $('#history-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  $('#history-clear').addEventListener('click', async () => {
    if (!window.confirm('Delete all saved checks from this device?')) return;
    await fetch('/api/history', { method: 'DELETE' });
    loadHistory();
  });

  updateCount();
})();
