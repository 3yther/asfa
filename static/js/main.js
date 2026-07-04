/* ASFA — JARVIS HUD main.js */
"use strict";

// ── State ──────────────────────────────────────────────────────────────────────
let ORB_STATE = "idle"; // idle | listening | speaking
const SCORE_CIRCUMFERENCE = 2 * Math.PI * 72; // r=72

// ── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initClock();
  initOrbCanvas();
  initOrbDust();
  initOrbActivity();
  initOrbClick();
  buildScoreTicks();
  initNav();
  loadAll();
  initTelemetry();      // Phase D — per-card pulse + freshness stamps + fresh/stale states
  initMission();        // Phase D — mission status bar + health indicators
  setStatusDate();
  wireControls();
  // Trading-bot data auto-refreshes every 10 minutes.
  setInterval(fetchBots, 10 * 60 * 1000);
});

// ── Header / card controls ──────────────────────────────────────────────────────
function wireControls() {
  const refresh = document.getElementById("briefing-refresh");
  if (refresh) refresh.addEventListener("click", refreshBriefing);
  const play = document.getElementById("briefing-play");
  if (play) play.addEventListener("click", playBriefing);
  const botsRefresh = document.getElementById("bots-refresh");
  if (botsRefresh) botsRefresh.addEventListener("click", fetchBots);
  const bell = document.getElementById("bell-btn");
  if (bell) bell.addEventListener("click", toggleNotif);
  const notifClear = document.getElementById("notif-clear");
  if (notifClear) notifClear.addEventListener("click", () => { markRead(); fetchNotifications(); });
  // Hydration quick-add buttons (+250 / +500 / +750).
  document.querySelectorAll(".water-add").forEach(btn => {
    btn.addEventListener("click", () => addWater(parseInt(btn.dataset.ml, 10)));
  });
  Think.wire();
  LockIn.wire();
  wireObsidian();
  wireBodyComp();
}

// ── Obsidian sync (manual) ───────────────────────────────────────────────────────
function wireObsidian() {
  const b = document.getElementById("obsidian-btn");
  if (b) {
    b.addEventListener("click", async () => {
      const label = b.textContent;
      b.disabled = true; b.textContent = "SYNCING…";
      try {
        const d = await apiPost("/api/asfa/obsidian/sync-now", {});
        if (d.status === "synced") {
          const extra = d.agents ? ` · ${d.agents} agents` : "";
          toast("OBSIDIAN · " + d.file + extra);
        } else {
          toast(d.message || "Sync failed — run ASFA on your Mac");
        }
      } catch { toast("Sync failed"); }
      b.textContent = label; b.disabled = false;
    });
  }
  const open = document.getElementById("obsidian-open-btn");
  if (open) {
    open.addEventListener("click", async () => {
      try {
        const d = await apiPost("/api/asfa/obsidian/open", {});
        if (d.status === "opened") toast("OBSIDIAN · opening vault");
        else toast(d.message || "Open the vault from your Mac");
      } catch { toast("Couldn't open vault"); }
    });
  }
}

// ── Hydration ───────────────────────────────────────────────────────────────────
const WATER_TARGET = 2000;

async function addWater(ml) {
  if (!ml || ml <= 0) return;
  const buttons = document.querySelectorAll(".water-add");
  buttons.forEach(b => (b.disabled = true));
  try {
    const d = await apiPost("/api/asfa/water-intake", {
      amount: ml,
      timestamp: new Date().toISOString(),
    });
    if (d.error) throw new Error(d.error);
    renderWater(d.total_ml || 0, d.target_ml || WATER_TARGET, d.streak || 0);
    toast(`+${ml}ML — ${d.total_ml}/${d.target_ml || WATER_TARGET}ML`);
  } catch (err) {
    toast("WATER LOG FAILED");
  } finally {
    buttons.forEach(b => (b.disabled = false));
  }
}

function playBriefing() {
  const el = document.getElementById("briefing-body");
  const text = el ? el.textContent : "";
  if (!text || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

// ── Clock ──────────────────────────────────────────────────────────────────────
function initClock() {
  function tick() {
    const now = new Date();
    const h  = String(now.getHours()).padStart(2, "0");
    const m  = String(now.getMinutes()).padStart(2, "0");
    const s  = String(now.getSeconds()).padStart(2, "0");
    const ms = String(now.getMilliseconds()).padStart(3, "0");
    const el = document.getElementById("clock");
    if (el) el.textContent = `${h}:${m}:${s}.${ms}`;
  }
  tick();
  setInterval(tick, 50);
}

function setStatusDate() {
  const el = document.getElementById("status-date");
  if (!el) return;
  const d = new Date();
  el.textContent = d.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" }).toUpperCase();
}

// ── Orb canvas particle field ──────────────────────────────────────────────────
function initOrbCanvas() {
  const canvas = document.getElementById("orb-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  const CX = W / 2, CY = H / 2, R = W / 2;

  const NODES = 24;
  const nodes = Array.from({ length: NODES }, () => randomNode(CX, CY, R));
  const edges = buildEdges(nodes, R * 0.7);

  let frame = 0;

  function randomNode(cx, cy, r) {
    const a = Math.random() * Math.PI * 2;
    const d = Math.random() * r * 0.75;
    return {
      x: cx + Math.cos(a) * d,
      y: cy + Math.sin(a) * d,
      vx: (Math.random() - 0.5) * 0.18,
      vy: (Math.random() - 0.5) * 0.18,
    };
  }

  function buildEdges(ns, maxDist) {
    const es = [];
    for (let i = 0; i < ns.length; i++)
      for (let j = i + 1; j < ns.length; j++)
        if (dist(ns[i], ns[j]) < maxDist) es.push([i, j]);
    return es;
  }

  function dist(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);

    // Orb background
    const grad = ctx.createRadialGradient(CX, CY, 0, CX, CY, R);
    grad.addColorStop(0,   "rgba(34,211,238,0.32)");
    grad.addColorStop(0.5, "rgba(79,70,229,0.18)");
    grad.addColorStop(1,   "rgba(6,182,212,0.08)");
    ctx.save();
    ctx.beginPath();
    ctx.arc(CX, CY, R, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.restore();

    // Move nodes
    for (const n of nodes) {
      n.x += n.vx;
      n.y += n.vy;
      const dx = n.x - CX, dy = n.y - CY;
      if (dx * dx + dy * dy > (R * 0.72) * (R * 0.72)) {
        n.vx = -n.vx * 0.8;
        n.vy = -n.vy * 0.8;
      }
    }

    // Edges
    for (const [i, j] of edges) {
      const a = nodes[i], b = nodes[j];
      const d = dist(a, b);
      if (d > R * 0.75) continue;
      const alpha = 0.12 * (1 - d / (R * 0.75));
      const pulse = 0.5 + 0.5 * Math.sin(frame * 0.03 + i);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = `rgba(6,182,212,${alpha * pulse})`;
      ctx.lineWidth = 0.6;
      ctx.stroke();
    }

    // Nodes
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const pulse = 0.5 + 0.5 * Math.sin(frame * 0.04 + i * 0.7);
      ctx.beginPath();
      ctx.arc(n.x, n.y, 1.2 + pulse * 0.6, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(103,232,249,${0.55 + pulse * 0.4})`;
      ctx.fill();
    }

    // Listening ripple
    if (ORB_STATE === "listening") {
      const rippleR = 30 + 50 * ((frame * 0.8 % 60) / 60);
      const rippleA = 0.35 * (1 - rippleR / 80);
      ctx.beginPath();
      ctx.arc(CX, CY, rippleR, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(124,58,237,${rippleA})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    frame++;
    requestAnimationFrame(draw);
  }

  draw();
}

// ── Orb dust — slow, sparse particles drifting around the orb ───────────────────
function initOrbDust() {
  const canvas = document.getElementById("orb-dust");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  const N = 34;
  const motes = Array.from({ length: N }, () => ({
    x: Math.random() * W,
    y: Math.random() * H,
    r: 0.5 + Math.random() * 1.3,
    vx: (Math.random() - 0.5) * 0.12,
    vy: (Math.random() - 0.5) * 0.12,
    a: 0.06 + Math.random() * 0.22,
    tw: Math.random() * Math.PI * 2,
  }));

  function draw() {
    ctx.clearRect(0, 0, W, H);
    for (const m of motes) {
      m.x += m.vx; m.y += m.vy; m.tw += 0.01;
      if (m.x < 0) m.x = W; else if (m.x > W) m.x = 0;
      if (m.y < 0) m.y = H; else if (m.y > H) m.y = 0;
      const alpha = m.a * (0.6 + 0.4 * Math.sin(m.tw));
      ctx.beginPath();
      ctx.arc(m.x, m.y, m.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(103,232,249,${alpha})`;
      ctx.fill();
    }
    requestAnimationFrame(draw);
  }
  draw();
}

// ── Activity-reactive pulse + critical (red) mode ───────────────────────────────
function _parseAgentTs(s) {
  if (!s) return 0;
  // SQLite "now" gives "YYYY-MM-DD HH:MM:SS" (UTC, no tz); ISO writes are local.
  const iso = s.includes("T") ? s : s.replace(" ", "T") + "Z";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

async function pollOrbActivity() {
  const stage = document.getElementById("orb-stage");
  const orb = document.getElementById("orb");
  if (!stage || !orb) return;

  let agents = [], alerts = [];
  try {
    const d = await apiGet("/api/agents");
    agents = d.agents || [];
    alerts = d.alerts || [];
  } catch { return; /* leave last-known cadence on a failed poll */ }

  // Critical state: any crit-level alert (overdue task, system/deploy error, …).
  const critical = alerts.some(a => (a.level || "").toLowerCase() === "crit");
  orb.classList.toggle("orb-critical", critical);

  const now = Date.now();
  let inLastMin = 0, inLast5Min = 0;
  for (const a of agents) {
    if (a.status === "locked") continue;
    const age = now - _parseAgentTs(a.last_active);
    if (age >= 0 && age < 60 * 1000) inLastMin++;
    if (age >= 0 && age < 5 * 60 * 1000) inLast5Min++;
  }

  // Critical pulses fastest; otherwise rate scales with recent agent activity.
  let cycle;
  if (critical)            cycle = "1s";
  else if (inLastMin >= 2) cycle = "1.5s"; // multiple agents in last minute
  else if (inLast5Min > 0) cycle = "3s";   // agent ran in last 5 min
  else                     cycle = "6s";   // calm idle
  stage.style.setProperty("--pulse-cycle", cycle);
}

function initOrbActivity() {
  if (!document.getElementById("orb-stage")) return;
  pollOrbActivity();
  setInterval(pollOrbActivity, 20 * 1000);
}

// ── Orb click → voice ─────────────────────────────────────────────────────────
function initOrbClick() {
  const orb = document.getElementById("orb");
  if (!orb) return;

  let mediaRec = null, chunks = [];

  orb.addEventListener("click", async () => {
    if (ORB_STATE === "idle") {
      setOrbState("listening");
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRec = new MediaRecorder(stream);
        chunks = [];
        mediaRec.ondataavailable = e => chunks.push(e.data);
        mediaRec.onstop = async () => {
          const blob = new Blob(chunks, { type: "audio/webm" });
          const text = await transcribeBlob(blob);
          if (text) await sendChat(text);
          setOrbState("idle");
        };
        mediaRec.start();
      } catch {
        toast("Microphone access denied");
        setOrbState("idle");
      }
    } else if (ORB_STATE === "listening") {
      if (mediaRec && mediaRec.state === "recording") {
        mediaRec.stop();
        mediaRec.stream.getTracks().forEach(t => t.stop());
        setOrbState("speaking");
      }
    }
  });
}

function setOrbState(state) {
  ORB_STATE = state;
  const orb = document.getElementById("orb");
  if (!orb) return;
  // Swap voice state without clobbering the activity-driven `orb-critical` class.
  orb.classList.remove("orb-idle", "orb-listening", "orb-speaking");
  orb.classList.add(`orb-${state}`);
}

async function transcribeBlob(blob) {
  const fd = new FormData();
  fd.append("audio", blob, "voice.webm");
  try {
    const r = await fetch("/api/transcribe", { method: "POST", body: fd });
    if (!r.ok) return null;
    return (await r.json()).text || null;
  } catch { return null; }
}

// ── Score ring tick marks (injected by JS) ─────────────────────────────────────
function buildScoreTicks() {
  const svg = document.getElementById("score-ring-svg");
  if (!svg) return;
  const NS = "http://www.w3.org/2000/svg";
  const cx = 90, cy = 90, outerR = 88;
  for (let i = 0; i < 60; i++) {
    const angle = (i / 60) * Math.PI * 2 - Math.PI / 2;
    const isMajor = i % 5 === 0;
    const r1 = isMajor ? outerR - 5 : outerR - 2;
    const x1 = cx + Math.cos(angle) * r1;
    const y1 = cy + Math.sin(angle) * r1;
    const x2 = cx + Math.cos(angle) * outerR;
    const y2 = cy + Math.sin(angle) * outerR;
    const line = document.createElementNS(NS, "line");
    line.setAttribute("x1", x1); line.setAttribute("y1", y1);
    line.setAttribute("x2", x2); line.setAttribute("y2", y2);
    line.setAttribute("stroke", isMajor ? "rgba(6,182,212,0.45)" : "rgba(6,182,212,0.18)");
    line.setAttribute("stroke-width", isMajor ? "1.2" : "0.7");
    svg.appendChild(line);
  }
}

// ── Water arc gauge ────────────────────────────────────────────────────────────
function updateWaterArc(ml, targetMl) {
  const arc = document.getElementById("water-arc");
  if (!arc) return;
  // Matches the SVG: r=40 → full circumference 251.3, visible 270° arc = 188.5.
  const FULL = 251.3, VISIBLE = 188.5;
  const pct = Math.min(ml / (targetMl || WATER_TARGET), 1);
  arc.style.strokeDasharray = `${(VISIBLE * pct).toFixed(1)} ${FULL}`;

  let colour;
  if (pct < 0.5) colour = "rgba(124,58,237,0.9)";
  else if (pct < 1) colour = "rgba(6,182,212,0.9)";
  else colour = "rgba(63,185,80,0.9)";
  arc.style.stroke = colour;
}

// ── Nav ────────────────────────────────────────────────────────────────────────
function initNav() {
  const btns = document.querySelectorAll(".nav-btn");
  const pill = document.querySelector(".nav-pill");
  const nav  = document.querySelector(".bottom-nav");
  if (!btns.length) return;

  function movePill(btn) {
    if (!pill || !nav) return;
    const navRect = nav.getBoundingClientRect();
    const btnRect = btn.getBoundingClientRect();
    pill.style.left  = (btnRect.left - navRect.left) + "px";
    pill.style.width = btnRect.width + "px";
  }

  btns.forEach(btn => {
    btn.addEventListener("click", () => {
      btns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      movePill(btn);
      document.body.setAttribute("data-tab", btn.dataset.tab);
    });
  });

  const active = document.querySelector(".nav-btn.active") || btns[0];
  active.classList.add("active");
  document.body.setAttribute("data-tab", active.dataset.tab || "home");
  setTimeout(() => movePill(active), 50);
}

// ── Card data-update pulse ──────────────────────────────────────────────────────
// Highlights a card briefly whenever its content changes (fresh data arriving),
// so motion is purposeful rather than decorative. Bursts are debounced to one pulse.
// ── Phase D — live telemetry: per-card pulse, "updated X ago", fresh/stale ──────
function initTelemetry() {
  const cards = [...document.querySelectorAll(".grid .card")];

  function agoText(ms) {
    const s = Math.floor(ms / 1000);
    if (s < 5)  return "just now";
    if (s < 60) return s + "s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    return Math.floor(m / 60) + "h ago";
  }

  const tracked = cards.map(card => {
    const stamp = document.createElement("div");
    stamp.className = "card-stamp";
    stamp.textContent = "updated just now";
    card.appendChild(stamp);
    const rec = { card, stamp, last: Date.now(), pulseT: null };

    const obs = new MutationObserver(muts => {
      // Ignore the once-per-second stamp text we write ourselves.
      if (muts.every(m => stamp === m.target || stamp.contains(m.target))) return;
      rec.last = Date.now();
      if (!rec.pulseT) {
        card.classList.add("pulse");
        rec.pulseT = setTimeout(() => { card.classList.remove("pulse"); rec.pulseT = null; }, 1100);
      }
    });
    obs.observe(card, { childList: true, subtree: true, characterData: true });
    return rec;
  });

  function tick() {
    const now = Date.now();
    for (const r of tracked) {
      const age = now - r.last;
      const txt = "updated " + agoText(age);
      if (r.stamp.textContent !== txt) r.stamp.textContent = txt;
      r.card.classList.toggle("is-fresh", age < 60 * 1000);
      r.card.classList.toggle("is-stale", age > 5 * 60 * 1000);
    }
  }
  tick();
  setInterval(tick, 1000);
}

// ── Phase D — mission status bar (day / time / status) + health indicators ──────
function initMission() {
  const readout   = document.getElementById("mb-readout");
  const indicator = document.getElementById("mb-indicator");
  if (!readout) return;

  const LAUNCH = Date.UTC(2026, 5, 11);   // Day 0 — 2026-06-11 (month is 0-based)
  let status = "ALL SYSTEMS NOMINAL";
  let state  = "nominal";                 // nominal | warn | crit

  function setHealth(key, st) {
    const dot = document.querySelector(`.mb-hpod[data-key="${key}"] .mb-hdot`);
    if (dot) dot.dataset.state = st;
  }

  function tick() {
    const now = new Date();
    const day = Math.floor((Date.now() - LAUNCH) / 86400000);
    const hh = String(now.getUTCHours()).padStart(2, "0");
    const mm = String(now.getUTCMinutes()).padStart(2, "0");
    readout.textContent = `MISSION DAY ${day} // ${hh}:${mm} UTC // ${status}`;
    if (indicator) indicator.dataset.state = state === "nominal" ? "nominal" : state;
  }

  async function poll() {
    let alerts = [];
    try {
      const d = await apiGet("/api/agents");
      alerts = d.alerts || [];
      setHealth("connectivity", "ok");      // a successful round-trip = link is up
    } catch {
      setHealth("connectivity", "crit");
      status = "ATTENTION REQUIRED"; state = "crit";
      setHealth("security", "warn");
      tick();
      return;
    }
    const lvl = a => (a.level || a.severity || "").toLowerCase();
    const crit = alerts.some(a => lvl(a) === "crit" || lvl(a) === "critical");
    const warn = alerts.some(a => ["warn", "warning", "high", "med", "medium"].includes(lvl(a)));
    if (crit)      { status = "ATTENTION REQUIRED"; state = "crit"; }
    else if (warn) { status = "ALL SYSTEMS NOMINAL"; state = "warn"; }
    else           { status = "ALL SYSTEMS NOMINAL"; state = "nominal"; }
    // Security health mirrors Sentinel / alert findings.
    setHealth("security", crit ? "crit" : warn ? "warn" : "ok");
    tick();
  }

  setHealth("power", "ok");   // power is always green while the page is running
  tick();
  setInterval(tick, 1000);
  poll();
  setInterval(poll, 30 * 1000);
}

// ── Load all data ──────────────────────────────────────────────────────────────
// Trigger a brief glow pulse on a card when its data updates (Visual Pass 3+4).
// Cards opt in via a data-card="<id>" attribute in the template.
function glowCard(cardId) {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const card = document.querySelector(`[data-card="${cardId}"]`);
  if (!card) return;
  card.classList.remove('card-updated');
  void card.offsetWidth; // force reflow so the animation re-triggers
  card.classList.add('card-updated');
}

// Card-id → data loader, for cards that own a single dedicated fetch. Tier 3
// Part 3 uses this to (a) skip a collapsed card's fetch on load — saving the
// rate budget — and (b) fetch it lazily when the user expands it. Keys match the
// data-card-id set by the layout script (element id minus "-card").
const CARD_LOADERS = {
  briefing: fetchBriefing, score: fetchScore, bots: fetchBots,
  systems: fetchSystems, validation: fetchValidation,
  "scout-pipeline": fetchScoutPipeline, calendar: fetchCalendar,
  inbox: fetchEmails, news: fetchNews, scent: fetchScent,
  money: fetchMoney, goals: fetchGoals, reflection: fetchReflection,
  supplements: fetchSupplements, bodycomp: fetchBodyComp,
};

function loadAll() {
  const collapsed = window.__asfaCollapsed || (() => false);
  for (const [cardId, fn] of Object.entries(CARD_LOADERS)) {
    if (!collapsed(cardId)) fn();
  }
  // Not gated: multi-card habits (water+sleep), gym (separate page seam), the
  // local market clock, and global/non-card widgets.
  fetchHabits();
  fetchGym();
  initChat();
  fetchNotifications();
  initSpotify();
  fetchFocusLine();
  fetchFocusToday();
  initMarketClock();
}

// Expanding a collapsed card (re)loads its data, since it was skipped on load.
document.addEventListener("asfa:card-expand", (e) => {
  const fn = CARD_LOADERS[e.detail && e.detail.cardId];
  if (fn) fn();
});

// ── Body composition (Renpho manual entry) ──────────────────────────────────────
let bodyCompChart = null;
async function fetchBodyComp() {
  const card = document.getElementById("bodycomp-card");
  if (!card) return;
  let data;
  try { data = await apiGet("/api/body-composition"); } catch (e) { return; }
  const latest = data.latest || null;
  const setVal = (id, v, suffix) => {
    const el = document.getElementById(id);
    if (el) el.textContent = (v == null || v === "") ? "—" : (v + suffix);
  };
  setVal("bc-weight", latest && latest.weight_kg, " KG");
  setVal("bc-bf", latest && latest.body_fat_percent, "%");
  setVal("bc-ffm", latest && latest.ffm_kg, " KG");

  // API returns newest-first; chart wants oldest→newest.
  const scans = (data.scans || []).slice().reverse();
  const points = scans.filter(s => s.weight_kg != null || s.body_fat_percent != null);
  const canvas = document.getElementById("bodycomp-chart");
  const empty = document.getElementById("bodycomp-empty");
  if (points.length < 5) {                    // waiting state, not a blank chart
    if (canvas) canvas.style.display = "none";
    if (empty) empty.hidden = false;
    if (bodyCompChart) { bodyCompChart.destroy(); bodyCompChart = null; }
    return;
  }
  if (canvas) canvas.style.display = "";
  if (empty) empty.hidden = true;
  const labels = points.map(s => String(s.date_scanned).slice(5));
  if (bodyCompChart) bodyCompChart.destroy();
  bodyCompChart = new Chart(canvas.getContext("2d"), {
    type: "line",
    data: { labels, datasets: [
      { label: "Mass (kg)", data: points.map(s => s.weight_kg), borderColor: "#00d9ff",
        backgroundColor: "rgba(0,217,255,.1)", yAxisID: "y", tension: .3, spanGaps: true },
      { label: "Body fat (%)", data: points.map(s => s.body_fat_percent), borderColor: "#f5c542",
        backgroundColor: "rgba(245,197,66,.1)", yAxisID: "y1", tension: .3, spanGaps: true },
    ]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#9fb2c0", font: { size: 10 } } } },
      scales: {
        y:  { position: "left",  ticks: { color: "#00d9ff" }, grid: { color: "rgba(255,255,255,.05)" } },
        y1: { position: "right", ticks: { color: "#f5c542" }, grid: { drawOnChartArea: false } },
        x:  { ticks: { color: "#8aa4b0", maxRotation: 0, autoSkip: true }, grid: { color: "rgba(255,255,255,.04)" } },
      },
    },
  });
}

function wireBodyComp() {
  const form = document.getElementById("bodycomp-form");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const num = id => { const v = parseFloat(document.getElementById(id)?.value); return isNaN(v) ? null : v; };
    const body = {
      date: document.getElementById("bc-in-date")?.value || undefined,
      weight_kg: num("bc-in-weight"), body_fat_percent: num("bc-in-bf"),
      ffm_kg: num("bc-in-ffm"), bmi: num("bc-in-bmi"),
      body_water_percent: num("bc-in-water"), bmr: num("bc-in-bmr"),
    };
    const metricKeys = ["weight_kg", "body_fat_percent", "ffm_kg", "bmi", "body_water_percent", "bmr"];
    if (metricKeys.every(k => body[k] == null)) { toast("ENTER A METRIC"); return; }
    try {
      await apiPost("/api/body-composition/manual", body);
      toast("SCAN LOGGED");
      form.reset();
      const d = document.getElementById("bodycomp-details"); if (d) d.open = false;
      fetchBodyComp();
    } catch (err) { toast("LOG FAILED"); }
  });
}

// ── Scout Pipeline ─────────────────────────────────────────────────────────────
const SP_STAGES = [["saved", "Saved"], ["applied", "Applied"], ["interview", "Interview"],
                   ["offer", "Offer"], ["rejected", "Rejected"]];
function spDaysAgo(iso) {
  if (!iso) return null;
  const d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
  return isNaN(d) ? null : Math.floor((Date.now() - d.getTime()) / 86400000);
}
async function fetchScoutPipeline() {
  const stagesEl = document.getElementById("sp-stages");
  const nudgesEl = document.getElementById("sp-nudges");
  if (!stagesEl) return;
  try {
    const [jobs, reminders] = await Promise.all([
      apiGet("/api/scout/pipeline"),
      apiGet("/api/scout/pipeline/reminders"),
    ]);
    const counts = {}; SP_STAGES.forEach(([k]) => counts[k] = 0);
    jobs.forEach(j => { if (counts[j.stage] != null) counts[j.stage]++; });
    stagesEl.innerHTML = SP_STAGES.map(([k, l]) =>
      `<div class="sp-stage"><span class="sp-num">${counts[k]}</span><span class="sp-lbl">${l}</span></div>`).join("");
    if (!reminders.length) {
      nudgesEl.innerHTML = `<div class="sp-none">No follow-ups due 🎉</div>`;
    } else {
      nudgesEl.innerHTML = `<div class="sp-nudge-head">NEEDS FOLLOW-UP</div>` +
        reminders.slice(0, 3).map(r => {
          const d = spDaysAgo(r.date_applied);
          return `<div class="sp-nudge"><span class="sp-nudge-job">${esc(r.job_title)} — ${esc(r.company)}</span>` +
                 `<span class="sp-nudge-days">nudge · ${d == null ? "?" : d}d</span></div>`;
        }).join("");
    }
    glowCard("scout-pipeline");
  } catch { /* silent */ }
}

// ── Briefing ───────────────────────────────────────────────────────────────────
async function fetchBriefing() {
  try {
    const d = await apiGet("/api/briefing");
    const el = document.getElementById("briefing-body");
    if (el) el.textContent = d.text || d.content || "— NO SIGNAL —";
    const uv = document.getElementById("uptime-val");
    if (uv) uv.textContent = uptime();
    const ls = document.getElementById("last-sync");
    if (ls) ls.textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    glowCard("briefing");
  } catch { /* silent */ }
}

function uptime() {
  const sec = Math.floor(performance.now() / 1000);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return `${String(h).padStart(2,"0")}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`;
}

// ── Score ──────────────────────────────────────────────────────────────────────
async function fetchScore() {
  try {
    const d = await apiGet("/api/score");
    renderScore(d);
    glowCard("score");
  } catch { /* silent */ }
}

function renderScore(d) {
  const score = d.score || 0;
  const ring = document.querySelector(".ring-fg");
  const numEl = document.getElementById("score-num");
  const colourEl = document.querySelector(".score-num");

  if (ring) {
    ring.style.strokeDashoffset = SCORE_CIRCUMFERENCE * (1 - score / 100);
    ring.style.stroke = scoreColour(score);
  }
  if (numEl) numEl.textContent = String(score).padStart(3, "0");
  if (colourEl) colourEl.style.color = scoreColour(score);

  const bd = d.breakdown || {};
  [
    { label: "HYDRATION",  key: "water" },
    { label: "SLEEP",      key: "sleep" },
    { label: "NUTRITION",  key: "nutrition" },
    { label: "MOVEMENT",   key: "movement" },
    { label: "REFLECTION", key: "reflection" },
  ].forEach(({ key }) => {
    const row = document.querySelector(`[data-contrib="${key}"]`);
    if (!row) return;
    const pct = bd[key] || 0;
    const fill = row.querySelector(".contrib-fill");
    const pctEl = row.querySelector(".contrib-pct");
    if (fill) fill.style.width = pct + "%";
    if (pctEl) pctEl.textContent = pct + "%";
  });
}

function scoreColour(s) {
  if (s >= 80) return "var(--green)";
  if (s >= 50) return "var(--gold)";
  return "var(--red)";
}

// ── Bots ───────────────────────────────────────────────────────────────────────
async function fetchBots() {
  const body = document.getElementById("bots-body");
  if (!body) return;
  try {
    const d = await apiGet("/api/asfa/bot-status");
    body.innerHTML = renderTradingActivity(d);
    glowCard("bots");
  } catch {
    // Even on failure, fall back to the static dashboard links.
    body.innerHTML = renderBotLinks({
      crypto: "https://stock-scanner-production-0b0d.up.railway.app/crypto",
      scanner: "https://stock-scanner-production-0b0d.up.railway.app/scanner",
    }) + `<div class="t-offline">> LIVE STATS OFFLINE</div>`;
  }
}

function renderBotLinks(links, active) {
  links = links || {};
  return `
  <div class="bot-links${active ? " is-active" : ""}">
    <a class="bot-link" href="${esc(links.crypto || "#")}" target="_blank" rel="noopener">
      <span class="bot-link-icon">◈</span>
      <span class="bot-link-name">CRYPTO BOT</span>
      <span class="bot-link-go">OPEN ▸</span>
    </a>
    <a class="bot-link" href="${esc(links.scanner || "#")}" target="_blank" rel="noopener">
      <span class="bot-link-icon">⬡</span>
      <span class="bot-link-name">STOCK SCANNER</span>
      <span class="bot-link-go">OPEN ▸</span>
    </a>
  </div>`;
}

function renderTradingActivity(d) {
  d = d || {};
  let html = renderBotLinks(d.links, d.online);

  if (!d.online) {
    return html + `<div class="t-offline">> LIVE STATS OFFLINE${d.error ? " — " + esc(d.error) : ""}</div>`;
  }

  const rows = [];
  const p = d.portfolio;
  if (p) {
    const pnl = p.total_pnl;
    const pnlNum = parseFloat(pnl);
    const pnlClass = isNaN(pnlNum) ? "" : (pnlNum >= 0 ? "pos" : "neg");
    const pct = (p.total_pnl_pct != null) ? ` (${esc(p.total_pnl_pct)}%)` : "";
    rows.push(`<div class="t-row"><span class="t-label">EQUITY      :</span><span class="t-val glow">$${esc(p.equity)}</span></div>`);
    rows.push(`<div class="t-row"><span class="t-label">TOTAL P&amp;L  :</span><span class="t-val ${pnlClass}">$${esc(pnl)}${pct}</span></div>`);
  }
  const sig = d.latest_signal;
  if (sig) {
    rows.push(`<div class="t-row"><span class="t-label">SIGNAL      :</span><span class="t-val">${esc(sig.symbol)} MSS ${esc(sig.direction)} @ ${esc(sig.price)}</span></div>`);
    if (sig.regime) rows.push(`<div class="t-row"><span class="t-label">REGIME      :</span><span class="t-val">${esc(sig.regime)}</span></div>`);
    if (sig.time) rows.push(`<div class="t-row"><span class="t-label">SIGNAL TIME :</span><span class="t-val">${fmtTs(sig.time)}</span></div>`);
  } else if (d.regime) {
    const rg = Object.entries(d.regime).map(([k, v]) => `${esc(k)}:${esc(v)}`).join("  ");
    rows.push(`<div class="t-row"><span class="t-label">REGIME      :</span><span class="t-val">${rg}</span></div>`);
  }

  if (rows.length) {
    html += `
    <div class="terminal" style="margin-top:10px">
      <div class="terminal-titlebar">
        <div class="terminal-dots"><span></span><span></span><span></span></div>
        <span class="terminal-name">CRYPTO BOT // LIVE</span>
        <span class="live-badge"><div class="pulse-dot"></div> LIVE</span>
      </div>
      <div class="terminal-body">${rows.join("")}</div>
    </div>`;
  }
  return html;
}

// ── Habits (water + sleep) ─────────────────────────────────────────────────────
async function fetchHabits() {
  try {
    const d = await apiGet("/api/habits");
    const today = d.today || {};
    renderWater(today.water_ml || 0, WATER_TARGET, d.water_streak || 0);
    renderSleep(today.sleep_hours || 0);
    glowCard("hydration");
  } catch { /* silent */ }
}

function renderWater(ml, target, streak) {
  updateWaterArc(ml, target);
  const numEl = document.getElementById("water-num");
  if (numEl) numEl.textContent = ml;
  const strkEl = document.getElementById("water-streak");
  if (strkEl) strkEl.textContent = streak ? `🔥 ${streak}` : "";
}

function renderSleep(hours) {
  const el = document.getElementById("sleep-val");
  if (el) el.textContent = hours ? `${hours}h` : "—";
  const bar = document.getElementById("sleep-bar");
  if (!bar) return;
  bar.style.width = Math.min((hours / 9) * 100, 100) + "%";
  bar.style.background = hours >= 7 ? "var(--green)" : hours >= 5 ? "var(--gold)" : "var(--red)";
}

// ── Calendar ───────────────────────────────────────────────────────────────────
async function fetchCalendar() {
  const el = document.getElementById("calendar-body");
  if (!el) return;
  try {
    const d = await apiGet("/api/calendar");
    if (!d.connected) { el.innerHTML = `<div class="list-item muted mono">// GOOGLE NOT LINKED</div>`; return; }
    const events = [...(d.today || []), ...(d.tomorrow || [])];
    el.innerHTML = events.length
      ? events.map(e => `<div class="list-item"><span class="time">${fmtTime(e.start)}</span><span>${esc(e.title)}</span></div>`).join("")
      : `<div class="list-item muted mono">// SCHEDULE CLEAR</div>`;
  } catch { el.innerHTML = `<div class="list-item muted">—</div>`; }
}

// ── Inbox (with AI summaries + Draft Reply) ─────────────────────────────────────
async function fetchEmails() {
  const el = document.getElementById("inbox-body");
  if (!el) return;
  try {
    const d = await apiGet("/api/emails");
    if (!d.connected) { el.innerHTML = `<div class="list-item muted mono">// COMMS NOT LINKED — <a href="/auth/google">CONNECT GOOGLE</a></div>`; return; }
    const emails = (d.emails || []).slice(0, 5);
    el.innerHTML = emails.length
      ? emails.map(emailCard).join("")
      : `<div class="list-item muted mono">// INBOX CLEAR</div>`;
    // Stash full email objects on their cards for the draft handler.
    el.querySelectorAll(".email-card").forEach((card, i) => { card.__email = emails[i]; });

    const sugWrap = document.getElementById("suggested-events");
    if (sugWrap && d.suggested_events && d.suggested_events.length) {
      const ev = d.suggested_events[0];
      sugWrap.innerHTML = `<div class="suggest-row"><span>📅 ${esc(ev.title)}</span><button class="btn btn-ghost" style="font-size:.55rem;padding:4px 8px;" onclick="addSuggested(this)">ADD EVENT</button></div>`;
      sugWrap.querySelector(".suggest-row button").__ev = ev;
    } else if (sugWrap) {
      sugWrap.innerHTML = "";
    }
  } catch { el.innerHTML = `<div class="list-item muted mono">// COMMS LINK FAILED</div>`; }
}

function emailCard(e) {
  const sender = esc((e.from || "").replace(/<.*>/, "").trim() || (e.from || "").split("@")[0]);
  return `
  <div class="email-card">
    <div class="email-head">
      <span class="email-from mono">${sender}</span>
      <span class="time">${fmtTs(e.date)}</span>
    </div>
    <div class="email-subject">${esc(e.subject || "—")}</div>
    <div class="email-summary muted">${esc(e.summary || e.snippet || "")}</div>
    <div class="email-actions">
      <button class="btn btn-ghost email-draft-btn" onclick="draftReply(this)">✎ DRAFT REPLY</button>
    </div>
    <div class="email-draft hidden"></div>
  </div>`;
}

// ── Email draft generator ───────────────────────────────────────────────────────
async function draftReply(btn) {
  const card = btn.closest(".email-card");
  const email = card && card.__email;
  if (!email) return;
  const box = card.querySelector(".email-draft");
  btn.disabled = true;
  btn.textContent = "✎ DRAFTING…";
  box.classList.remove("hidden");
  box.innerHTML = `<span class="muted mono">GENERATING REPLY…</span>`;
  try {
    const d = await apiPost("/api/asfa/draft-reply", { email_id: email.id });
    if (d.error) throw new Error(d.error);
    renderDraft(box, d.draft || "");
  } catch (err) {
    box.innerHTML = `<span class="muted mono">// DRAFT FAILED — ${esc(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "✎ DRAFT REPLY";
  }
}

function renderDraft(box, draft) {
  box.innerHTML = `
    <div class="draft-text mono">${esc(draft)}</div>
    <textarea class="draft-edit hidden">${esc(draft)}</textarea>
    <div class="draft-actions">
      <button class="btn btn-ghost" onclick="copyDraft(this)">⧉ COPY TO GMAIL</button>
      <button class="btn btn-ghost" onclick="editDraft(this)">✎ EDIT</button>
    </div>`;
}

function copyDraft(btn) {
  const box = btn.closest(".email-draft");
  const edit = box.querySelector(".draft-edit");
  const view = box.querySelector(".draft-text");
  const text = (edit && !edit.classList.contains("hidden")) ? edit.value : (view ? view.textContent : "");
  navigator.clipboard?.writeText(text).then(
    () => toast("DRAFT COPIED — PASTE INTO GMAIL"),
    () => toast("COPY FAILED")
  );
}

function editDraft(btn) {
  const box = btn.closest(".email-draft");
  const edit = box.querySelector(".draft-edit");
  const view = box.querySelector(".draft-text");
  if (!edit || !view) return;
  if (edit.classList.contains("hidden")) {
    edit.classList.remove("hidden");
    view.classList.add("hidden");
    edit.focus();
    btn.textContent = "✓ DONE";
  } else {
    view.textContent = edit.value;
    edit.classList.add("hidden");
    view.classList.remove("hidden");
    btn.textContent = "✎ EDIT";
  }
}

async function addSuggested(btn) {
  const ev = btn.__ev;
  if (!ev) return;
  await apiPost("/api/calendar", ev);
  toast("EVENT ADDED");
  fetchCalendar();
}

// ── News ───────────────────────────────────────────────────────────────────────
async function fetchNews() {
  const grid   = document.getElementById("news-headlines");
  const ticker = document.getElementById("ticker-content");
  if (!grid && !ticker) return;
  try {
    const d = await apiGet("/api/news");
    const articles = [...(d.top || []), ...(d.finance || [])].slice(0, 6);
    if (grid) {
      grid.innerHTML = articles.slice(0, 3).map(a =>
        `<a class="intel-article" href="${esc(a.url || "#")}" target="_blank" rel="noopener">
          <span class="intel-ts">${fmtTs(a.published_at || a.publishedAt || "")}</span>
          <h3>${esc(a.title || "")}</h3>
        </a>`
      ).join("");
    }
    if (ticker && articles.length) {
      const txt = articles.map(a => esc(a.title || "")).join('<span class="ticker-sep">///</span>');
      ticker.innerHTML = txt + '<span class="ticker-sep">///</span>' + txt;
    }
    glowCard("news");
  } catch { /* silent */ }
}

// ── Money ──────────────────────────────────────────────────────────────────────
async function fetchMoney() {
  try {
    const d = await apiGet("/api/money");
    const weekEl  = document.getElementById("money-week");
    const monthEl = document.getElementById("money-month");
    if (weekEl)  weekEl.textContent  = `£${(d.total || 0).toFixed(2)}`;
    if (monthEl) monthEl.textContent = `£${(d.monthly_total || 0).toFixed(2)}`;
    const listEl = document.getElementById("money-list");
    if (listEl) {
      const cats = Object.entries(d.by_category || {}).slice(0, 5);
      listEl.innerHTML = cats.map(([cat, amt]) =>
        `<div class="list-item"><span class="time">${esc(cat.toUpperCase())}</span><span class="mono">£${amt.toFixed(2)}</span></div>`
      ).join("") || `<div class="list-item muted mono">// NO DATA</div>`;
    }
    glowCard("money");
  } catch { /* silent */ }
}

// ── Gym / PBs ──────────────────────────────────────────────────────────────────
async function fetchGym() {
  try {
    const d = await apiGet("/api/gym");
    const pbsList = document.getElementById("pbs-list");
    if (pbsList) {
      const pbs = d.pbs || [];
      pbsList.innerHTML = pbs.slice(0, 6).map(p =>
        `<div class="list-item"><span class="time">${esc((p.exercise || "").toUpperCase())}</span><span class="mono green-text">${p.best_weight || "—"}kg × ${p.best_reps || "—"}</span></div>`
      ).join("") || `<div class="list-item muted mono">// NO RECORDS</div>`;
    }
    const bwEl = document.getElementById("bodyweight-val");
    if (bwEl && d.body_weight && d.body_weight.length) {
      bwEl.textContent = d.body_weight[d.body_weight.length - 1].weight_kg + " KG";
    }
  } catch { /* silent */ }
}

// ── Today's Scent (compact fragrance recommendation) ───────────────────────────
async function fetchScent() {
  const body = document.getElementById("scent-body");
  if (!body) return;
  try {
    const rec = await apiGet("/api/fragrances/recommendation");
    const f = rec.fragrance, ctx = rec.context || {}, r = rec.routine || {};
    const ctxBits = [ctx.time_bucket, ctx.temp_c != null ? `${Math.round(ctx.temp_c)}°C` : "", ctx.condition]
      .filter(Boolean).join(" · ");
    // Two key routine steps: the wash + whichever moisturiser the pairing uses.
    const steps = [r.shower_gel, r.body_lotion || r.body_oil].filter(Boolean)
      .map(p => `${esc(p.brand)} ${esc(p.name)}`).join(" → ");
    body.innerHTML = `
      <div class="scent-line">
        <div>
          <div class="scent-name">${f.is_signature ? "⭐ " : ""}💨 ${esc(f.name)}</div>
          <div class="scent-context mono">${esc(ctxBits)}</div>
          ${steps ? `<div class="scent-steps">${steps} → ${esc(f.name)}</div>` : ""}
        </div>
        <button class="btn btn-grad scent-wear" id="scent-wear-btn">✓ WEAR</button>
      </div>`;
    const btn = document.getElementById("scent-wear-btn");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const h = new Date().getHours();
        const bucket = h >= 5 && h < 11 ? "morning" : h < 17 ? "day" : h < 22 ? "evening" : "night";
        await apiPost(`/api/fragrances/${f.id}/wear`, { time_of_day: bucket });
        toast(`💨 ${f.name} logged`);
        fetchScent(); // re-fetch: today's pick is now penalised, show the next one
      } catch {
        toast("Could not log wear");
        btn.disabled = false;
      }
    });
    glowCard("scent");
  } catch {
    body.innerHTML = `<span class="muted">// SCENT VAULT OFFLINE</span>`;
  }
}

// ── Reflection ─────────────────────────────────────────────────────────────────
async function fetchReflection() {
  try {
    const d = await apiGet("/api/reflection");
    const list = Array.isArray(d) ? d : [];
    const latest = list[0];
    const scoreEl = document.getElementById("refl-score");
    if (scoreEl) scoreEl.textContent = latest ? `${latest.score}/10` : "—";
    const textEl = document.getElementById("refl-text");
    if (textEl) textEl.textContent = latest ? latest.content : "// NO ENTRY TODAY";
  } catch { /* silent */ }
}

// ── Goals ──────────────────────────────────────────────────────────────────────
async function fetchGoals() {
  const el = document.getElementById("goals-list");
  if (!el) return;
  try {
    const d = await apiGet("/api/goals");
    const goals = Array.isArray(d) ? d : [];
    el.innerHTML = goals.map(g => `
      <div class="goal-row">
        <div class="goal-top">
          <span>${esc(g.title)}</span>
          <span class="mono" style="color:var(--cyan)">${Number(g.progress) || 0}%</span>
        </div>
        <div class="progress"><div class="progress-fill" style="width:${Number(g.progress) || 0}%"></div></div>
      </div>`
    ).join("") || `<div class="muted mono">// NO ACTIVE OBJECTIVES</div>`;
  } catch { el.innerHTML = `<div class="muted">—</div>`; }
}

// ── Supplements ─────────────────────────────────────────────────────────────────
// Daily checklist; status is filtered server-side by today's date so the
// boxes reset naturally at the day rollover.
async function fetchSupplements() {
  const el = document.getElementById("supplements-body");
  if (!el) return;
  try {
    const d = await apiGet("/api/supplements");
    renderSupplements(el, d);
    glowCard("supplements");
  } catch { el.innerHTML = `<div class="muted mono">// SUPPLEMENTS OFFLINE</div>`; }
}

function renderSupplements(el, d) {
  const items = (d && d.items) || [];
  el.innerHTML = items.map(it => `
    <button class="supp-row${it.taken ? " taken" : ""}" data-name="${esc(it.name)}"
            onclick="toggleSupplement(this)" type="button">
      <span class="supp-box">${it.taken ? "✓" : ""}</span>
      <span class="supp-name">${esc(it.label)}</span>
      <span class="supp-time mono">${it.taken ? fmtTime(it.taken_at) : ""}</span>
    </button>`
  ).join("");
  const count = document.getElementById("supp-count");
  if (count) count.textContent = `${d.taken_count || 0}/${d.total || items.length}`;
  const strk = document.getElementById("supp-streak");
  if (strk) strk.textContent = d.streak ? `🔥 ${d.streak}` : "";
}

async function toggleSupplement(btn) {
  const name = btn.dataset.name;
  const taken = !btn.classList.contains("taken");
  btn.disabled = true;
  try {
    const d = await apiPost("/api/supplements", { name, taken });
    renderSupplements(document.getElementById("supplements-body"), d);
    toast(taken ? "SUPPLEMENT LOGGED" : "SUPPLEMENT CLEARED");
  } catch {
    toast("FAILED");
    btn.disabled = false;
  }
}

// ── Spotify — status indicator + auto-resume on load ────────────────────────────
async function initSpotify() {
  const chip = document.getElementById("spotify-chip");
  if (!chip) return;  // not connected → header shows the CONNECT::SPOTIFY link
  let status;
  try { status = await apiGet("/api/asfa/spotify/status"); }
  catch { return; }
  renderSpotify(status);

  // Auto-resume only when connected and nothing is currently playing — so we
  // don't yank a track that's already going.
  if (status.connected && !status.is_playing) {
    try {
      const r = await apiPost("/api/asfa/spotify/play", {});
      if (r.ok) {
        setTimeout(async () => {
          try { renderSpotify(await apiGet("/api/asfa/spotify/status")); } catch {}
        }, 1200);
      } else if (r.reason === "no_device" || r.reason === "reauth") {
        toast(r.message);
      }
      if (r.message) chip.title = r.message;
    } catch { /* silent — indicator already reflects state */ }
  }
}

function renderSpotify(s) {
  const dot = document.getElementById("spotify-dot");
  const label = document.getElementById("spotify-label");
  const chip = document.getElementById("spotify-chip");
  if (!dot || !chip) return;
  dot.classList.remove("dot-playing", "dot-paused", "dot-idle");
  if (s.is_playing) {
    dot.classList.add("dot-playing");
    if (label) label.textContent = "PLAYING";
    if (s.track) chip.title = `${s.track} — ${s.artist || ""}`.trim();
  } else {
    dot.classList.add("dot-paused");
    if (label) label.textContent = "SPOTIFY";
    chip.title = s.device ? "Paused" : "No active device";
  }
}

// ── Market session clock (client-side, live countdown) ──────────────────────────
let _marketTimer = null;
function initMarketClock() {
  if (!document.getElementById("market-status")) return;
  renderMarketClock();
  // Session status changes at most twice a day, so poll once a MINUTE, not
  // once a second. And clear any existing timer before setting a new one:
  // loadAll() can run again on refresh/nav, and without this guard each call
  // stacked another 1s interval, so multiple ticks raced to rewrite the same
  // element — the flicker.
  if (_marketTimer) clearInterval(_marketTimer);
  _marketTimer = setInterval(renderMarketClock, 60000);
}

// Current wall-clock in US Eastern, DST-safe via Intl (no manual offset math).
function _etNow() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).formatToParts(new Date());
  const get = (t) => parts.find(p => p.type === t)?.value;
  const wd = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }[get("weekday")];
  let h = parseInt(get("hour"), 10); if (h === 24) h = 0;
  return { dow: wd, secs: h * 3600 + parseInt(get("minute"), 10) * 60 + parseInt(get("second"), 10) };
}

function _fmtDur(secs) {
  secs = Math.max(0, Math.round(secs));
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  // Minute granularity: we now render once a minute, so showing live seconds
  // would just look frozen/jumpy.
  return m > 0 ? `${m}m` : "under 1m";
}

function renderMarketClock() {
  const statusEl = document.getElementById("market-status");
  const countEl = document.getElementById("market-count");
  const dot = document.getElementById("market-dot");
  if (!statusEl) { if (_marketTimer) { clearInterval(_marketTimer); _marketTimer = null; } return; }
  const OPEN = 9 * 3600 + 30 * 60, CLOSE = 16 * 3600;   // 09:30–16:00 ET
  const { dow, secs } = _etNow();
  const weekday = dow >= 1 && dow <= 5;
  let state, status, count;

  if (weekday && secs >= OPEN && secs < CLOSE) {
    state = "open";
    status = "US MARKET OPEN";
    count = "closes in " + _fmtDur(CLOSE - secs);
  } else if (weekday && secs < OPEN) {
    state = "pre";
    status = "US MARKET OPENS IN";
    count = _fmtDur(OPEN - secs);
  } else {
    // After close today, or weekend → find the next weekday open.
    state = "closed";
    let daysAhead = 1;
    // if it's a weekday before close already handled; here we're after close or weekend
    let d = dow;
    // advance to next day until it's Mon–Fri
    let added = 0;
    do { d = (d + 1) % 7; added++; } while (!(d >= 1 && d <= 5));
    daysAhead = added;
    const secsToOpen = (daysAhead * 86400) - secs + OPEN;
    if (dow === 0 || dow === 6) {
      status = "MARKET CLOSED";
      count = "opens " + (dow === 6 ? "Monday" : "Monday");
    } else {
      status = "US MARKET CLOSED";
      count = "opens in " + _fmtDur(secsToOpen);
    }
  }
  // Only write to the DOM when a value actually changes. Rewriting identical
  // textContent/classes on every tick is what made the indicator flash; this
  // keeps the element in place and only updates its content when needed.
  if (statusEl.textContent !== status) statusEl.textContent = status;
  if (countEl.textContent !== count) countEl.textContent = count;
  if (dot) {
    const open = state === "open";
    if (dot.classList.contains("market-open") !== open) {
      dot.classList.toggle("market-open", open);
      dot.classList.toggle("market-closed", !open);
    }
  }
}

// ── Trading systems health glance ───────────────────────────────────────────────
async function fetchSystems() {
  const el = document.getElementById("systems-body");
  if (!el) return;
  try {
    const d = await apiGet("/api/asfa/bots-health");
    const bots = (d && d.bots) || [];
    el.innerHTML = bots.map(b => `
      <a class="system-row" href="${esc(b.url || "#")}" target="_blank" rel="noopener">
        <span class="system-dot ${b.online ? "on" : "off"}"></span>
        <span class="system-name">${esc(b.name)}</span>
        <span class="system-meta mono">${esc(b.online ? (b.last_signal || b.status || "online") : "offline")}</span>
      </a>`
    ).join("") || `<span class="muted mono">// NO SYSTEMS</span>`;
    glowCard("systems");
  } catch { el.innerHTML = `<span class="muted mono">// LINK DOWN</span>`; }
}

// ── Validation countdown ─────────────────────────────────────────────────────────
async function fetchValidation() {
  const dayEl = document.getElementById("val-day");
  const fill = document.getElementById("val-fill");
  if (!dayEl) return;
  try {
    const d = await apiGet("/api/asfa/validation");
    if (d.complete) {
      dayEl.textContent = `VALIDATION COMPLETE · ${d.total}/${d.total}`;
    } else if (d.not_started) {
      dayEl.textContent = `VALIDATION: starts ${d.start}`;
    } else {
      dayEl.textContent = `VALIDATION: Day ${d.day} of ${d.total}`;
    }
    if (fill) fill.style.width = `${d.pct || 0}%`;
    glowCard("validation");
  } catch { dayEl.textContent = "VALIDATION: —"; }
}

// ── "What now?" focus line ───────────────────────────────────────────────────────
async function fetchFocusLine() {
  const el = document.getElementById("focus-line");
  if (!el) return;
  try {
    const d = await apiGet("/api/asfa/focus-line");
    el.textContent = d.text || "—";
    el.classList.toggle("urgent", !!d.urgent);
  } catch { el.textContent = "—"; }
}

async function fetchFocusToday() {
  const el = document.getElementById("focus-today-chip");
  if (!el) return;
  try {
    const d = await apiGet("/api/asfa/focus/today");
    el.textContent = "◷ " + fmtFocus(d.focus_seconds_today || 0);
  } catch { /* leave default */ }
}

function fmtFocus(secs) {
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
  return `${h}h ${m}m`;
}

function fmtClock(secs) {
  const m = Math.floor(secs / 60), s = secs % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// Calm ascending chime (shared by Think timer + Pomodoro transitions).
function playChime() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    const ctx = new AC();
    [523.25, 659.25, 783.99].forEach((f, i) => {   // C5 · E5 · G5
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.value = f;
      const t0 = ctx.currentTime + i * 0.18;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.linearRampToValueAtTime(0.16, t0 + 0.04);
      g.gain.exponentialRampToValueAtTime(0.0006, t0 + 1.4);
      o.connect(g); g.connect(ctx.destination);
      o.start(t0); o.stop(t0 + 1.5);
    });
    setTimeout(() => { try { ctx.close(); } catch {} }, 2200);
  } catch { /* audio blocked — silent */ }
}

// ── THINK MODE — calm full-screen breathing space ────────────────────────────────
const Think = (function () {
  const C = 2 * Math.PI * 100;   // ring circumference (r=100 in the SVG viewBox)
  let open = false, raf = 0, endAt = 0, durMs = 0, ambientOn = false;
  const $ = (id) => document.getElementById(id);

  function openMode() {
    const m = $("think-mode");
    if (!m || open) return;
    open = true;
    m.classList.add("think-open");
    m.setAttribute("aria-hidden", "false");
    document.body.classList.add("think-active");
    resetRing();
    // Defer wiring exit so the opening interaction doesn't immediately close it.
    setTimeout(() => {
      m.addEventListener("click", onClick);
      document.addEventListener("keydown", onKey);
    }, 60);
  }

  function closeMode() {
    const m = $("think-mode");
    if (!m || !open) return;
    open = false;
    m.classList.remove("think-open");
    m.setAttribute("aria-hidden", "true");
    document.body.classList.remove("think-active");
    cancelTimer();
    m.removeEventListener("click", onClick);
    document.removeEventListener("keydown", onKey);
  }

  function onClick(e) { if (!e.target.closest(".think-controls")) closeMode(); }
  function onKey(e) { if (e.key === "Escape") closeMode(); }

  function resetRing() {
    const p = $("think-ring-prog");
    if (p) { p.style.strokeDasharray = C; p.style.strokeDashoffset = 0; p.style.opacity = 0; }
    const r = $("think-remaining"); if (r) r.textContent = "";
  }
  function cancelTimer() { if (raf) { cancelAnimationFrame(raf); raf = 0; } durMs = 0; }

  function setTimer(min) {
    cancelTimer();
    durMs = min * 60 * 1000;
    endAt = performance.now() + durMs;
    const p = $("think-ring-prog");
    if (p) { p.style.strokeDasharray = C; p.style.opacity = 1; }
    tick();
  }
  function tick() {
    raf = requestAnimationFrame(tick);
    const remain = Math.max(0, endAt - performance.now());
    const frac = durMs > 0 ? (1 - remain / durMs) : 0;   // 0→1 elapsed
    const p = $("think-ring-prog");
    if (p) p.style.strokeDashoffset = C * frac;           // ring depletes
    const r = $("think-remaining");
    if (r) {
      const s = Math.ceil(remain / 1000);
      r.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    }
    if (remain <= 0) { cancelTimer(); playChime(); if (r) r.textContent = "DONE"; }
  }

  async function toggleAmbient() {
    const b = $("think-ambient");
    if (!b) return;
    ambientOn = !ambientOn;
    b.classList.toggle("on", ambientOn);
    if (!ambientOn) return;
    try {
      const res = await apiPost("/api/asfa/spotify/focus", { q: "ambient focus" });
      if (!res.ok) { if (res.message) toast(res.message); ambientOn = false; b.classList.remove("on"); }
    } catch { toast("Spotify unavailable"); ambientOn = false; b.classList.remove("on"); }
  }

  function wire() {
    const btn = $("think-btn");
    if (btn) btn.addEventListener("click", openMode);
    document.querySelectorAll(".think-tbtn").forEach(b => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        document.querySelectorAll(".think-tbtn").forEach(x => x.classList.remove("on"));
        b.classList.add("on");
        setTimer(parseInt(b.dataset.min, 10));
      });
    });
    const amb = $("think-ambient");
    if (amb) amb.addEventListener("click", (e) => { e.stopPropagation(); toggleAmbient(); });
  }
  return { wire };
})();

// ── LOCK IN — focus session (count-up timer + music + dim) ────────────────────────
const LockIn = (function () {
  const WORK = 50 * 60, BREAK = 10 * 60;
  let active = false, startTs = 0, raf = 0, pomodoro = false, phase = "work", phaseStart = 0;
  const $ = (id) => document.getElementById(id);

  async function start() {
    if (active) return;
    active = true; startTs = Date.now(); phaseStart = startTs; phase = "work";
    const bar = $("lockin-bar");
    bar.classList.add("lockin-on");
    bar.setAttribute("aria-hidden", "false");
    document.body.classList.add("locked-in");
    const orb = $("orb-stage"); if (orb) orb.classList.add("orb-focus");
    update();
    try {
      const res = await apiPost("/api/asfa/spotify/focus", { q: "deep focus" });
      if (!res.ok && res.reason !== "not_connected" && res.message) toast(res.message);
    } catch { /* music optional */ }
    toast("LOCKED IN");
  }

  async function end() {
    if (!active) return;
    active = false;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    const dur = Math.round((Date.now() - startTs) / 1000);
    const bar = $("lockin-bar");
    bar.classList.remove("lockin-on");
    bar.setAttribute("aria-hidden", "true");
    document.body.classList.remove("locked-in");
    const orb = $("orb-stage"); if (orb) orb.classList.remove("orb-focus");
    const ph = $("lockin-phase"); if (ph) ph.textContent = "";
    try {
      const d = await apiPost("/api/asfa/focus/session", { duration_seconds: dur });
      const chip = $("focus-today-chip");
      if (chip && d.focus_seconds_today != null) chip.textContent = "◷ " + fmtFocus(d.focus_seconds_today);
    } catch { /* keep session locally even if log fails */ }
    toast("SESSION LOGGED · " + fmtClock(dur));
  }

  function update() {
    raf = requestAnimationFrame(update);
    const elapsed = Math.floor((Date.now() - startTs) / 1000);
    const t = $("lockin-time"); if (t) t.textContent = fmtClock(elapsed);
    const ph = $("lockin-phase");
    if (pomodoro) {
      const inPhase = Math.floor((Date.now() - phaseStart) / 1000);
      const limit = phase === "work" ? WORK : BREAK;
      const left = Math.max(0, limit - inPhase);
      if (ph) ph.textContent = (phase === "work" ? "WORK " : "BREAK ") + fmtClock(left);
      if (left <= 0) { playChime(); phase = phase === "work" ? "break" : "work"; phaseStart = Date.now(); }
    } else if (ph) { ph.textContent = ""; }
  }

  function togglePomo() {
    pomodoro = !pomodoro;
    const b = $("lockin-pomo");
    if (b) { b.classList.toggle("on", pomodoro); b.setAttribute("aria-pressed", pomodoro ? "true" : "false"); }
    if (pomodoro) { phase = "work"; phaseStart = Date.now(); }
  }

  function wire() {
    const s = $("lockin-btn"); if (s) s.addEventListener("click", start);
    const e = $("lockin-end"); if (e) e.addEventListener("click", end);
    const p = $("lockin-pomo"); if (p) p.addEventListener("click", togglePomo);
  }
  return { wire };
})();

// ── Notifications ──────────────────────────────────────────────────────────────
async function fetchNotifications() {
  try {
    const d = await apiGet("/api/notifications");
    const badge = document.getElementById("notif-badge");
    if (badge) {
      badge.textContent = d.unread || "";
      badge.style.display = d.unread ? "inline-flex" : "none";
    }
    const list = document.getElementById("notif-list");
    if (list) {
      list.innerHTML = (d.notifications || []).slice(0, 10).map(n =>
        `<div class="notif-item${n.is_read ? "" : " unread"}">${esc(n.message)}<span class="time">${fmtTs(n.created_at)}</span></div>`
      ).join("") || `<div class="notif-item muted">// ALL CLEAR</div>`;
    }
  } catch { /* silent */ }
}

function toggleNotif() {
  const panel = document.getElementById("notif-panel");
  if (!panel) return;
  panel.classList.toggle("hidden");
  if (!panel.classList.contains("hidden")) {
    markRead();
    fetchNotifications();
  }
}

async function markRead() {
  try { await apiPost("/api/notifications/read", {}); } catch { /* silent */ }
}

// ── Chat ───────────────────────────────────────────────────────────────────────
function initChat() {
  const input = document.getElementById("chat-input");
  const btn   = document.getElementById("chat-send");
  if (!input || !btn) return;

  async function send() {
    const msg = input.value.trim();
    if (!msg) return;
    input.value = "";
    appendMsg("user", msg);
    const thinking = appendMsg("ai", "▋");
    try {
      const d = await apiPost("/api/chat", { message: msg });
      thinking.textContent = d.reply || "...";
      if (d.actions && d.actions.length) {
        appendMsg("action", "ACTION: " + d.actions.join(" / "));
        loadAll();
      }
    } catch {
      thinking.textContent = "// ERROR — NO RESPONSE";
    }
    scrollChat();
  }

  btn.addEventListener("click", send);
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });

  fetchHistory();
}

async function fetchHistory() {
  try {
    const msgs = await apiGet("/api/conversation");
    if (!Array.isArray(msgs)) return;
    const log = document.getElementById("chat-log");
    if (!log) return;
    msgs.slice(-20).forEach(m => appendMsg(m.role === "user" ? "user" : "ai", m.content));
    scrollChat();
  } catch { /* silent */ }
}

function appendMsg(type, text) {
  const log = document.getElementById("chat-log");
  if (!log) return null;
  const div = document.createElement("div");
  div.className = `msg msg-${type}`;
  div.textContent = text;
  log.appendChild(div);
  scrollChat();
  return div;
}

function scrollChat() {
  const log = document.getElementById("chat-log");
  if (log) log.scrollTop = log.scrollHeight;
}

async function sendChat(text) {
  appendMsg("user", text);
  setOrbState("speaking");
  try {
    const d = await apiPost("/api/chat", { message: text });
    appendMsg("ai", d.reply || "");
    if (d.actions && d.actions.length) {
      appendMsg("action", "ACTION: " + d.actions.join(" / "));
      loadAll();
    }
  } catch { /* silent */ }
  setOrbState("idle");
}

// ── Form helpers ───────────────────────────────────────────────────────────────
async function logWater(ml) {
  if (!ml) ml = parseInt(document.getElementById("water-ml")?.value || 0, 10);
  if (!ml) return;
  await apiPost("/api/habits/water", { ml });
  toast(`+${ml}ML LOGGED`);
  fetchHabits();
}

async function logSleep() {
  const h = parseFloat(document.getElementById("sleep-hours")?.value || 0);
  if (!h) return;
  await apiPost("/api/habits/sleep", { hours: h });
  toast(`${h}H SLEEP LOGGED`);
  fetchHabits();
}

async function logWeight() {
  const kg = parseFloat(document.getElementById("weight-kg")?.value || 0);
  if (!kg) return;
  await apiPost("/api/gym/weight", { weight_kg: kg });
  toast(`${kg}KG LOGGED`);
  fetchGym();
}

async function logSpend() {
  const amount   = parseFloat(document.getElementById("spend-amount")?.value || 0);
  const category = document.getElementById("spend-category")?.value || "other";
  const note     = document.getElementById("spend-note")?.value || "";
  if (!amount) return;
  await apiPost("/api/money", { amount, category, note });
  toast(`£${amount.toFixed(2)} LOGGED`);
  fetchMoney();
}

async function saveReflection() {
  const score   = parseInt(document.getElementById("refl-score-in")?.value || 5, 10);
  const content = document.getElementById("refl-content")?.value || "";
  await apiPost("/api/reflection", { score, content });
  toast("REFLECTION SAVED");
  fetchReflection();
}

async function addGoal() {
  const titleIn  = document.getElementById("goal-title");
  const targetIn = document.getElementById("goal-target");
  const title = titleIn?.value?.trim();
  if (!title) return;
  await apiPost("/api/goals", { title, target: targetIn?.value?.trim() || "" });
  if (titleIn) titleIn.value = "";
  toast("OBJECTIVE ADDED");
  fetchGoals();
}

async function refreshBriefing() {
  const btn = document.getElementById("briefing-refresh");
  const el = document.getElementById("briefing-body");
  if (btn) btn.classList.add("btn-spinning");
  if (el) el.innerHTML = `<span class="muted">REGENERATING BRIEFING…</span>`;
  try {
    const d = await apiGet("/api/briefing?refresh=1");
    if (el) el.textContent = d.text || d.content || "—";
  } catch {
    if (el) el.innerHTML = `<span class="muted">// BRIEFING UNAVAILABLE</span>`;
  } finally {
    if (btn) btn.classList.remove("btn-spinning");
  }
}

// ── HTTP helpers ───────────────────────────────────────────────────────────────
async function apiGet(url) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

// ── Utility ────────────────────────────────────────────────────────────────────
function toast(msg, ms = 2200) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

// Escapes quotes too — esc() output is interpolated into double-quoted
// attributes (href/data-name), where an unescaped `"` breaks out.
function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtTime(dt) {
  if (!dt) return "—";
  try { return new Date(dt).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }); }
  catch { return "—"; }
}

function fmtTs(dt) {
  if (!dt) return "—";
  try {
    const d = new Date(dt);
    const diffH = (Date.now() - d) / 3600000;
    if (diffH < 1)  return `${Math.floor(diffH * 60)}m`;
    if (diffH < 24) return `${Math.floor(diffH)}h`;
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }).toUpperCase();
  } catch { return "—"; }
}

// Add corner elements to all sci-fi panels
document.querySelectorAll('.sci-fi-panel').forEach(el => {
  ['corner-bl', 'corner-br'].forEach(cls => {
    const div = document.createElement('div');
    div.className = cls;
    el.appendChild(div);
  });
});
