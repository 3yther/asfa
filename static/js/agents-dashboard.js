/* ASFA — Agents dashboard
 * Renders the heartbeat summary + a grid of agent cards driven by:
 *   GET /api/agents/status            → heartbeat (status, energy, budget health)
 *   GET /api/error-budgets            → success rates per agent
 *   GET /api/agents/<id>/diary        → most recent diary entry (on expand)
 *   GET /api/audit?agent_id=&limit=5  → recent audit entries (on expand)
 *   GET /api/agents/<id>/skills       → declared skills (on expand)
 * Standalone: this page does not load main.js, so we keep a tiny fetch helper.
 */
(function () {
  "use strict";

  const POLL_MS = 60000;

  // Friendly display names for the 13 functional agents (heartbeat ids).
  const AGENT_LABELS = {
    scout: "Scout",
    sentinel: "Sentinel",
    quant_bot: "Quant Bot",
    briefing: "Briefing",
    hydration: "Hydration",
    health: "Health",
    obsidian: "Obsidian",
    backup: "Backup",
    summary: "Summary",
    supplement: "Supplement",
    weekly_review: "Weekly Review",
    reflection: "Reflection",
    insights: "Insights",
  };

  // ── fetch helpers ────────────────────────────────────────────────────────
  async function apiGet(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }

  // ── formatting ───────────────────────────────────────────────────────────
  function label(id) {
    return AGENT_LABELS[id] || id.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // Parse a timestamp; SQLite gives "YYYY-MM-DD HH:MM:SS" (UTC, no tz marker),
  // Postgres/ISO may include "T"/offset. Treat naive strings as UTC.
  function parseTime(s) {
    if (!s || s === "Never") return null;
    let str = String(s).trim();
    if (str.indexOf("T") === -1) str = str.replace(" ", "T");
    if (!/[zZ]|[+-]\d\d:?\d\d$/.test(str)) str += "Z";
    const d = new Date(str);
    return isNaN(d.getTime()) ? null : d;
  }

  function relTime(s) {
    if (!s || s === "Never") return "Never";
    const d = parseTime(s);
    if (!d) return String(s);
    const secs = Math.floor((Date.now() - d.getTime()) / 1000);
    if (secs < 0) return "just now";
    if (secs < 45) return "a few seconds ago";
    const mins = Math.floor(secs / 60);
    if (mins < 60) return mins + "m ago";
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + "h ago";
    const days = Math.floor(hrs / 24);
    if (days === 1) return "Yesterday";
    if (days < 7) return days + "d ago";
    return d.toLocaleDateString();
  }

  function energyTier(e) {
    if (e > 60) return "high";
    if (e >= 30) return "mid";
    return "low";
  }

  // Regex-based (not the innerHTML round-trip, which leaves quotes intact):
  // esc() output lands inside double-quoted attributes like title="…".
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function addCorners(el) {
    ["corner-bl", "corner-br"].forEach((cls) => {
      const div = document.createElement("div");
      div.className = cls;
      el.appendChild(div);
    });
  }

  // ── state ────────────────────────────────────────────────────────────────
  const budgets = {}; // agent_id -> { current_rate, target, health }
  const cards = {}; // agent_id -> card element
  let firstRender = true;

  // ── heartbeat summary ────────────────────────────────────────────────────
  function renderHeartbeat(hb) {
    const bar = document.getElementById("heartbeat-summary");
    if (!bar) return;
    bar.classList.remove("hb-error");
    bar.innerHTML = `
      <div class="hb-item hb-total"><span class="hb-num">${hb.total}</span><span class="hb-lbl">agents</span></div>
      <div class="hb-item"><span class="hb-dot status-green"></span><span class="hb-num">${hb.healthy}</span><span class="hb-lbl">healthy</span></div>
      <div class="hb-item"><span class="hb-dot status-yellow"></span><span class="hb-num">${hb.warnings}</span><span class="hb-lbl">warnings</span></div>
      <div class="hb-item"><span class="hb-dot status-red"></span><span class="hb-num">${hb.critical}</span><span class="hb-lbl">critical</span></div>
      <div class="hb-item hb-checked"><span class="hb-lbl">last check</span><span class="hb-time">${esc(relTime(hb.checked_at))}</span></div>
    `;
    addCorners(bar);
  }

  function renderHeartbeatError() {
    const bar = document.getElementById("heartbeat-summary");
    if (!bar) return;
    bar.classList.add("hb-error");
    bar.innerHTML = `<div class="hb-item hb-fail">⚠ Unable to load agent heartbeat. Retrying…</div>`;
    addCorners(bar);
  }

  // ── status → colour map ──────────────────────────────────────────────────
  const STATUS_COLOR = { healthy: "green", warning: "yellow", critical: "red" };

  function successRate(id) {
    const b = budgets[id];
    if (!b || (b.total_runs || 0) === 0) return null;
    return b.current_rate;
  }

  // ── agent card (collapsed shell) ─────────────────────────────────────────
  function buildCard(res) {
    const id = res.agent_id;
    const card = document.createElement("div");
    card.className = "agent-card sci-fi-panel";
    card.dataset.agentId = id;
    card.dataset.expanded = "false";
    card.innerHTML = `
      <button class="agent-head" type="button" aria-expanded="false">
        <span class="agent-name">${esc(label(id))}</span>
        <span class="agent-head-right">
          <span class="status-indicator" data-status></span>
          <span class="agent-caret">▼</span>
        </span>
      </button>
      <div class="energy-row">
        <span class="energy-label">ENERGY</span>
        <div class="energy-meter" data-energy-meter>
          <div class="energy-fill" data-energy-fill></div>
        </div>
        <span class="energy-val" data-energy-val></span>
      </div>
      <div class="agent-lastact" data-lastact></div>
      <div class="agent-stats">
        <div class="stat"><span class="stat-lbl">Success</span><span class="stat-val" data-stat-success>—</span></div>
        <div class="stat"><span class="stat-lbl">Energy</span><span class="stat-val" data-stat-energy>—</span></div>
        <div class="stat"><span class="stat-lbl">Budget</span><span class="budget-health" data-stat-budget>—</span></div>
      </div>
      <div class="agent-detail" data-detail>
        <div class="agent-detail-inner" data-detail-inner></div>
      </div>
    `;
    addCorners(card);

    const head = card.querySelector(".agent-head");
    head.addEventListener("click", () => toggleCard(card));
    cards[id] = card;
    updateCard(card, res, false);
    return card;
  }

  // Soft-update a card's live fields (status, energy, stats) without rebuild.
  function updateCard(card, res, glow) {
    const id = res.agent_id;
    const energy = Number(res.energy) || 0;

    const dot = card.querySelector("[data-status]");
    dot.className = "status-indicator status-" + (STATUS_COLOR[res.status] || "green");
    dot.title = res.message || res.status;

    const fill = card.querySelector("[data-energy-fill]");
    const prevW = fill.style.width;
    fill.style.width = Math.max(0, Math.min(100, energy)) + "%";
    fill.className = "energy-fill energy-" + energyTier(energy);
    card.querySelector("[data-energy-val]").textContent = Math.round(energy);

    card.querySelector("[data-lastact]").textContent = "Last active: " + relTime(res.last_activity);

    card.querySelector("[data-stat-energy]").textContent = Math.round(energy);
    const sr = successRate(id);
    card.querySelector("[data-stat-success]").textContent = sr == null ? "—" : (sr * 100).toFixed(1) + "%";

    const bh = (budgets[id] && budgets[id].health) || res.budget_health || "healthy";
    const badge = card.querySelector("[data-stat-budget]");
    badge.textContent = bh;
    badge.className = "budget-health budget-" + bh;

    if (glow && prevW !== fill.style.width) {
      card.classList.remove("card-updated");
      void card.offsetWidth; // reflow to restart animation
      card.classList.add("card-updated");
    }
  }

  // ── expand / collapse ────────────────────────────────────────────────────
  function toggleCard(card) {
    const open = card.dataset.expanded === "true";
    if (open) {
      card.dataset.expanded = "false";
      card.classList.remove("expanded");
      card.querySelector(".agent-head").setAttribute("aria-expanded", "false");
      card.querySelector(".agent-caret").textContent = "▼";
    } else {
      card.dataset.expanded = "true";
      card.classList.add("expanded");
      card.querySelector(".agent-head").setAttribute("aria-expanded", "true");
      card.querySelector(".agent-caret").textContent = "▲";
      loadDetail(card);
    }
  }

  async function loadDetail(card) {
    const id = card.dataset.agentId;
    const inner = card.querySelector("[data-detail-inner]");
    if (card.dataset.loaded === "true") return;
    inner.innerHTML = `<div class="detail-loading">Loading details…</div>`;

    const [diary, audit, skills] = await Promise.all([
      apiGet(`/api/agents/${id}/diary`).catch((e) => ({ __err: e })),
      apiGet(`/api/audit?agent_id=${id}&limit=5`).catch((e) => ({ __err: e })),
      apiGet(`/api/agents/${id}/skills`).catch((e) => ({ __err: e })),
    ]);

    // If everything failed (e.g. session expired), show a single message.
    if (diary && diary.__err && audit && audit.__err && skills && skills.__err) {
      inner.innerHTML = `<div class="detail-error">Unable to load details</div>`;
      return;
    }
    card.dataset.loaded = "true";
    inner.innerHTML =
      diaryHTML(id, diary) + budgetStatusHTML(id) + auditHTML(audit) + skillsHTML(skills);

    const link = inner.querySelector("[data-diary-full]");
    if (link) link.addEventListener("click", (e) => { e.preventDefault(); openDiaryModal(id); });
  }

  function diaryHTML(id, diary) {
    let body, ts = "", hasFull = false;
    if (!diary || diary.__err || diary.error || !diary.summary) {
      body = `<span class="detail-empty">No entries yet</span>`;
    } else {
      window.__diaries = window.__diaries || {};
      window.__diaries[id] = diary;
      hasFull = true;
      const full = String(diary.summary);
      const excerpt = full.length > 200 ? full.slice(0, 200).trimEnd() + "…" : full;
      body = `<p class="diary-excerpt">${esc(excerpt)}</p>`;
      ts = diary.created_at ? `<span class="detail-ts">${esc(relTime(diary.created_at))}</span>` : "";
    }
    return `
      <section class="detail-block">
        <h4 class="detail-h">Diary ${ts}</h4>
        ${body}
        ${hasFull ? `<a href="#" class="diary-full-link" data-diary-full>Read full diary →</a>` : ""}
      </section>`;
  }

  function budgetStatusHTML(id) {
    const b = budgets[id];
    if (!b) return "";
    const cur = ((b.current_rate || 0) * 100).toFixed(1);
    const tgt = ((b.target || 0.95) * 100).toFixed(1);
    return `
      <section class="detail-block">
        <h4 class="detail-h">Error budget</h4>
        <p class="budget-line">
          <span class="budget-health budget-${b.health}">${cur}%</span>
          <span class="budget-target">/ ${tgt}% target</span>
          <span class="budget-runs">(${b.successful_runs || 0}/${b.total_runs || 0} runs)</span>
        </p>
      </section>`;
  }

  function auditHTML(audit) {
    let rows;
    if (!audit || audit.__err || !Array.isArray(audit) || audit.length === 0) {
      rows = `<tr><td colspan="3" class="detail-empty">No activity logged</td></tr>`;
    } else {
      rows = audit
        .map(
          (a) => `<tr>
            <td>${esc(a.action)}</td>
            <td class="audit-${esc(a.outcome)}">${esc(a.outcome)}</td>
            <td class="audit-time">${esc(relTime(a.created_at))}</td>
          </tr>`
        )
        .join("");
    }
    return `
      <section class="detail-block">
        <h4 class="detail-h">Recent audit log</h4>
        <table class="audit-table">
          <thead><tr><th>Action</th><th>Outcome</th><th>Time</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </section>`;
  }

  function skillsHTML(skills) {
    let body;
    if (!skills || skills.__err || !Array.isArray(skills) || skills.length === 0) {
      body = `<span class="detail-empty">No skills registered</span>`;
    } else {
      body = skills
        .map((s) => `<span class="skill-badge" title="${esc(s.description || "")}">${esc(s.skill_name)}</span>`)
        .join("");
    }
    return `
      <section class="detail-block">
        <h4 class="detail-h">Available skills</h4>
        <div class="skill-list">${body}</div>
      </section>`;
  }

  // ── full-diary modal ─────────────────────────────────────────────────────
  function openDiaryModal(id) {
    const diary = (window.__diaries || {})[id];
    if (!diary) return;
    let modal = document.getElementById("diary-modal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "diary-modal";
      modal.className = "diary-modal-overlay";
      modal.innerHTML = `<div class="diary-modal sci-fi-panel" role="dialog" aria-modal="true">
        <button class="diary-modal-close" type="button" aria-label="Close">✕</button>
        <h3 class="diary-modal-title"></h3>
        <div class="diary-modal-ts"></div>
        <div class="diary-modal-body"></div>
      </div>`;
      document.body.appendChild(modal);
      addCorners(modal.querySelector(".diary-modal"));
      const close = () => modal.classList.remove("open");
      modal.querySelector(".diary-modal-close").addEventListener("click", close);
      modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    }
    modal.querySelector(".diary-modal-title").textContent = label(id) + " — Diary";
    modal.querySelector(".diary-modal-ts").textContent = diary.created_at ? relTime(diary.created_at) : "";
    modal.querySelector(".diary-modal-body").textContent = diary.summary || "";
    modal.classList.add("open");
  }

  // ── data load ────────────────────────────────────────────────────────────
  async function loadBudgets() {
    try {
      const list = await apiGet("/api/error-budgets");
      if (Array.isArray(list)) list.forEach((b) => { budgets[b.agent_id] = b; });
    } catch (e) {
      /* non-fatal — cards fall back to heartbeat budget_health */
    }
  }

  async function loadStatus(soft) {
    let hb;
    try {
      hb = await apiGet("/api/agents/status");
    } catch (e) {
      if (!soft || firstRender) renderHeartbeatError();
      return;
    }
    renderHeartbeat(hb);

    const grid = document.getElementById("agents-grid");
    const results = hb.results || {};
    const order = Object.keys(AGENT_LABELS).filter((id) => results[id]);
    // append any unexpected agents not in the label map
    Object.keys(results).forEach((id) => { if (order.indexOf(id) === -1) order.push(id); });

    if (firstRender) {
      grid.innerHTML = "";
      order.forEach((id) => grid.appendChild(buildCard(results[id])));
      firstRender = false;
    } else {
      order.forEach((id) => {
        const card = cards[id];
        if (card) updateCard(card, results[id], true);
      });
    }
  }

  async function init() {
    await loadBudgets();
    await loadStatus(false);
    setInterval(async () => {
      await loadBudgets();
      await loadStatus(true);
    }, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
