/* ASFA — JARVIS HUD main.js */
"use strict";

// ── State ──────────────────────────────────────────────────────────────────────
let ORB_STATE = "idle"; // idle | listening | speaking
const SCORE_CIRCUMFERENCE = 2 * Math.PI * 72; // r=72

// ── Init ───────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  initClock();
  initOrbCanvas();
  initOrbClick();
  buildScoreTicks();
  initNav();
  loadAll();
  spawnParticles();
  setStatusDate();
});

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
    grad.addColorStop(0,   "rgba(124,58,237,0.35)");
    grad.addColorStop(0.5, "rgba(79,70,229,0.20)");
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
      ctx.fillStyle = `rgba(124,58,237,${0.6 + pulse * 0.4})`;
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
  orb.className = `orb orb-${state}`;
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
  const r = 44;
  const circumference = 2 * Math.PI * r * (270 / 360); // 270° arc
  const pct = Math.min(ml / targetMl, 1);
  const offset = circumference * (1 - pct);
  arc.style.strokeDasharray = circumference;
  arc.style.strokeDashoffset = offset;

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

// ── Ambient particles ──────────────────────────────────────────────────────────
function spawnParticles() {
  const container = document.getElementById("particles");
  if (!container) return;
  for (let i = 0; i < 30; i++) {
    const p = document.createElement("div");
    p.className = "particle";
    p.style.left = Math.random() * 100 + "vw";
    p.style.animationDuration = (12 + Math.random() * 20) + "s";
    p.style.animationDelay = (-Math.random() * 20) + "s";
    p.style.opacity = 0;
    container.appendChild(p);
  }
}

// ── Load all data ──────────────────────────────────────────────────────────────
function loadAll() {
  fetchBriefing();
  fetchScore();
  fetchBots();
  fetchHabits();
  fetchCalendar();
  fetchEmails();
  fetchNews();
  fetchMoney();
  fetchGym();
  fetchReflection();
  fetchGoals();
  fetchIdeas();
  initChat();
  fetchNotifications();
}

// ── Briefing ───────────────────────────────────────────────────────────────────
async function fetchBriefing() {
  try {
    const d = await apiGet("/api/briefing");
    const el = document.getElementById("briefing-text");
    if (el) el.textContent = d.plain_text || d.content || "— NO SIGNAL —";
    const uv = document.getElementById("uptime-val");
    if (uv) uv.textContent = uptime();
    const ls = document.getElementById("last-sync");
    if (ls) ls.textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
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
    const d = await apiGet("/api/bots");
    body.innerHTML = botRow("STOCK-SCANNER // SIGNAL PROC.", d.scanner);
  } catch {
    body.innerHTML = `<div class="t-offline">> CONNECTION FAILED</div>`;
  }
}

function botRow(name, b) {
  const online = b && b.online;

  if (!online) {
    return `
    <div class="terminal">
      <div class="terminal-titlebar">
        <div class="terminal-dots"><span></span><span></span><span></span></div>
        <span class="terminal-name">${name}</span>
        <span class="live-badge neg">&bull; OFFLINE</span>
      </div>
      <div class="terminal-body">
        <div class="t-offline">> SIGNAL LOST — ${esc((b && b.error) || "NO RESPONSE")}</div>
      </div>
    </div>`;
  }

  const equity    = b.equity    || b.portfolio_value || "—";
  const pnl       = b.pnl      || b.daily_pnl       || b.total_pnl || "—";
  const positions = b.positions || b.open_positions  || 0;
  const pnlNum    = parseFloat(pnl);
  const pnlClass  = isNaN(pnlNum) ? "" : (pnlNum >= 0 ? "pos" : "neg");

  return `
  <div class="terminal">
    <div class="terminal-titlebar">
      <div class="terminal-dots"><span></span><span></span><span></span></div>
      <span class="terminal-name">${name}</span>
      <span class="live-badge"><div class="pulse-dot"></div> LIVE</span>
    </div>
    <div class="terminal-body">
      <div class="t-row"><span class="t-label">EQUITY         :</span><span class="t-val glow">${esc(equity)}</span></div>
      <div class="t-row"><span class="t-label">DAILY P&amp;L    :</span><span class="t-val ${pnlClass}">${esc(pnl)}</span></div>
      <div class="t-row"><span class="t-label">OPEN POSITIONS :</span><span class="t-val">${positions}</span></div>
    </div>
  </div>`;
}

// ── Habits (water + sleep) ─────────────────────────────────────────────────────
async function fetchHabits() {
  try {
    const d = await apiGet("/api/habits");
    const today = d.today || {};
    renderWater(today.water_ml || 0, 2500, d.water_streak || 0);
    renderSleep(today.sleep_hours || 0);
  } catch { /* silent */ }
}

function renderWater(ml, target, streak) {
  updateWaterArc(ml, target);
  const valEl = document.getElementById("water-val");
  if (valEl) valEl.textContent = `${ml}ml`;
  const strkEl = document.getElementById("water-streak");
  if (strkEl) strkEl.textContent = streak;
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
  const el = document.getElementById("calendar-list");
  if (!el) return;
  try {
    const d = await apiGet("/api/calendar");
    if (!d.connected) { el.innerHTML = `<div class="list-item muted mono">// GOOGLE NOT LINKED</div>`; return; }
    const events = [...(d.today || []), ...(d.tomorrow || [])];
    el.innerHTML = events.length
      ? events.map(e => `<div class="list-item"><span class="time">${fmtTime(e.start)}</span><span>${esc(e.summary)}</span></div>`).join("")
      : `<div class="list-item muted mono">// SCHEDULE CLEAR</div>`;
  } catch { el.innerHTML = `<div class="list-item muted">—</div>`; }
}

// ── Inbox ──────────────────────────────────────────────────────────────────────
async function fetchEmails() {
  const el = document.getElementById("inbox-list");
  if (!el) return;
  try {
    const d = await apiGet("/api/emails");
    if (!d.connected) { el.innerHTML = `<div class="list-item muted mono">// COMMS NOT LINKED</div>`; return; }
    const emails = d.emails || [];
    el.innerHTML = emails.slice(0, 5).map(e =>
      `<div class="list-item"><span class="time">${esc((e.from || "").split("@")[0])}</span><a href="#">${esc(e.subject || "—")}</a></div>`
    ).join("") || `<div class="list-item muted mono">// INBOX CLEAR</div>`;
    if (d.suggested_events && d.suggested_events.length) {
      const ev = d.suggested_events[0];
      el.innerHTML += `<div class="suggest-row"><span>${esc(ev.title)}</span><button class="btn btn-ghost" style="font-size:.55rem;padding:4px 8px;" onclick="addSuggested(this)">ADD EVENT</button></div>`;
      el.querySelector(".suggest-row button").__ev = ev;
    }
  } catch { el.innerHTML = `<div class="list-item muted">—</div>`; }
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
          <span class="mono" style="color:var(--cyan)">${g.progress || 0}%</span>
        </div>
        <div class="progress"><div class="progress-fill" style="width:${g.progress || 0}%"></div></div>
      </div>`
    ).join("") || `<div class="muted mono">// NO ACTIVE OBJECTIVES</div>`;
  } catch { el.innerHTML = `<div class="muted">—</div>`; }
}

// ── Ideas ──────────────────────────────────────────────────────────────────────
async function fetchIdeas() {
  const el = document.getElementById("ideas-list");
  if (!el) return;
  try {
    const d = await apiGet("/api/ideas");
    const ideas = Array.isArray(d) ? d : [];
    el.innerHTML = ideas.slice(0, 8).map(i =>
      `<div class="list-item"><span class="time">${fmtTs(i.created_at)}</span><span>${esc(i.content)}</span></div>`
    ).join("") || `<div class="list-item muted mono">// NO IDEAS LOGGED</div>`;
  } catch { /* silent */ }
}

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

async function addIdea() {
  const input = document.getElementById("idea-input");
  const content = input?.value?.trim();
  if (!content) return;
  await apiPost("/api/ideas", { content });
  if (input) input.value = "";
  toast("IDEA LOGGED");
  fetchIdeas();
}

async function refreshBriefing() {
  const btn = document.getElementById("briefing-refresh-btn");
  if (btn) btn.classList.add("btn-spinning");
  try {
    const d = await apiGet("/api/briefing?refresh=1");
    const el = document.getElementById("briefing-text");
    if (el) el.textContent = d.plain_text || d.content || "—";
  } finally {
    if (btn) btn.classList.remove("btn-spinning");
  }
}

// ── HTTP helpers ───────────────────────────────────────────────────────────────
async function apiGet(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
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

function esc(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
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
