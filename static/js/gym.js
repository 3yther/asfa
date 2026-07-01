/* ══════════════════════════════════════════════════════════════════════════
   gym.js — Iron Log gym tracker frontend for ASFA.
   Vanilla JS, Chart.js for graphs. Self-contained (own api helpers so it can
   run on the standalone /gym page without main.js). Sections:
     0. helpers / config      4. workout engine
     1. tab routing           5. history
     2. dashboard             6. exercises
     3. bodygraph SVG         7. progress
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
"use strict";

/* ── 0. Helpers & config ──────────────────────────────────────────────── */
const API = "/api/gym";
async function apiGet(url) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
async function apiSend(url, method, body) {
  const opts = { method, credentials: "include" };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
const apiPost = (u, b) => apiSend(u, "POST", b);
const apiDel  = (u)    => apiSend(u, "DELETE");

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
const fmtKg = (n) => (Math.round((+n || 0) * 100) / 100).toString();
const round1 = (n) => Math.round((+n || 0) * 10) / 10;

const RANK_ORDER  = ["Bronze", "Silver", "Gold", "Platinum", "Diamond"];
const RANK_COLORS = { Bronze:"#cd7f32", Silver:"#c0c0c0", Gold:"#ffd700", Platinum:"#e5e4e2", Diamond:"#00d9ff" };
const RANK_ICON   = { Bronze:"🥉", Silver:"🥈", Gold:"🥇", Platinum:"💎", Diamond:"💠" };
const UNRANKED    = "#1a1a2e";

// Our muscle groups → body-highlighter MuscleType slugs (a group may map to
// several library regions). Reverse map turns a tapped slug back into a group.
const MUSCLE_TO_SLUGS = {
  chest: ["chest"], back: ["upper-back", "lower-back"],
  shoulders: ["front-deltoids", "back-deltoids"], biceps: ["biceps"],
  triceps: ["triceps"], quads: ["quadriceps"], hamstrings: ["hamstring"],
  calves: ["calves"], core: ["abs"],
};
const SLUG_TO_MUSCLE = {};
Object.entries(MUSCLE_TO_SLUGS).forEach(([g, slugs]) => slugs.forEach(s => SLUG_TO_MUSCLE[s] = g));

const LS_TARGETS  = "gym_routine_targets";      // { routineId: minutes }
const LS_AI       = "gym_ai_trainer_enabled";   // "1" | "0" (default off)
const LS_DELOAD   = "gym_deload_dismissed";     // ISO-week key of last dismissal
const CARDIO_DEFAULT_MIN = 30;

function isCardioEx(ex) {
  if (!ex) return false;
  return ex.is_cardio === true || ex.exercise_type === "cardio" || ex.muscle_group === "cardio";
}
function isoWeekKey(d) {
  d = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const wk = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
  return d.getUTCFullYear() + "-W" + wk;
}
function loadTargets() { try { return JSON.parse(localStorage.getItem(LS_TARGETS) || "{}"); } catch (e) { return {}; } }
function saveTarget(rid, mins) { const t = loadTargets(); t[rid] = mins; localStorage.setItem(LS_TARGETS, JSON.stringify(t)); }
function aiEnabled() { return localStorage.getItem(LS_AI) === "1"; }
const CYAN = "#00d9ff", GOLD = "#ffd700", VIOLET = "#7f77dd";
const ROTATION = ["push", "pull", "legs", "upper", "lower"];
const PLATES = [25, 20, 15, 10, 5, 2.5, 1.25];
const PLATE_COLORS = { 25:"#e23", 20:"#25c", 15:"#fb0", 10:"#2a5", 5:"#eee", 2.5:"#888", 1.25:"#c9a" };
const BAR_KG = 20;

function corners(elm) { ["corner-bl", "corner-br"].forEach(c => elm.appendChild(el("div", c))); }

function toast(msg, ms = 2200) {
  const t = el("div", "gym-toast", esc(msg));
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
function computeRank(ex, weight) {
  weight = +weight || 0; let rank = "Bronze";
  for (const r of RANK_ORDER) { const th = ex["rank_" + r.toLowerCase()]; if (th != null && weight >= th) rank = r; }
  return rank;
}
function oneRm(w, reps) { return round1((+w || 0) * (1 + (+reps || 0) / 30)); }

/* WebAudio — lazy, unlocked on first workout gesture */
let audioCtx = null;
function initAudio() { if (!audioCtx) { try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {} } }
function beep(freq = 660, dur = 0.16, type = "sine", when = 0) {
  if (!audioCtx) return;
  const o = audioCtx.createOscillator(), g = audioCtx.createGain();
  o.type = type; o.frequency.value = freq; o.connect(g); g.connect(audioCtx.destination);
  const t = audioCtx.currentTime + when;
  g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.start(t); o.stop(t + dur + 0.02);
}
function chime() { initAudio(); [880, 1108, 1318].forEach((f, i) => beep(f, 0.22, "triangle", i * 0.09)); }
function restBeep() { initAudio(); beep(720, 0.18, "sine", 0); beep(960, 0.2, "sine", 0.18); }

/* Global caches */
let EXERCISES = [];      // full library
let EX_BY_ID = {};
let ROUTINES = [];
let PR_BY_EX = {};       // exercise_id -> PR row (avoids per-exercise 404s)
let CHARTS = {};         // named Chart instances

async function loadPRs() {
  const prs = await apiGet(`${API}/prs`).catch(() => []);
  PR_BY_EX = {}; prs.forEach(p => PR_BY_EX[p.exercise_id] = p);
  return prs;
}

// Routine details never change during a session — cache them to avoid refetching
// (the workout tab would otherwise fire one request per routine on every visit).
const ROUTINE_CACHE = {};
async function getRoutineFull(id) {
  if (!ROUTINE_CACHE[id]) ROUTINE_CACHE[id] = await apiGet(`${API}/routines/${id}`);
  return ROUTINE_CACHE[id];
}

function killChart(name) { if (CHARTS[name]) { CHARTS[name].destroy(); delete CHARTS[name]; } }

/* ── 1. Tab routing ───────────────────────────────────────────────────── */
const loaded = {};
function switchTab(name) {
  $$(".gym-subtab").forEach(b => b.classList.toggle("active", b.dataset.subtab === name));
  $$(".gym-panel").forEach(p => p.classList.toggle("active", p.dataset.panel === name));
  if (window.SoundFX) try { SoundFX.play && SoundFX.play("tab"); } catch (e) {}
  if (LOADERS[name]) LOADERS[name]();       // (re)load each visit for freshness
  location.hash = name;
}
$$(".gym-subtab").forEach(b => b.addEventListener("click", () => switchTab(b.dataset.subtab)));

/* ══ 2. DASHBOARD ═════════════════════════════════════════════════════════ */
async function loadDashboard() {
  try {
    const [xp, ranks, prs, sessions, recovery, weekly, cal, body, deload, restDays] = await Promise.all([
      apiGet(`${API}/xp`), apiGet(`${API}/ranks`), apiGet(`${API}/prs`),
      apiGet(`${API}/sessions?limit=60`), apiGet(`${API}/muscle-recovery`),
      apiGet(`${API}/volume/weekly`), apiGet(`${API}/sessions/calendar?months=3`),
      apiGet(`${API}/body-stats?limit=1`),
      apiGet(`${API}/deload-check`).catch(() => ({})),
      apiGet(`${API}/rest-days`).catch(() => []),
    ]);
    PR_BY_EX = {}; prs.forEach(p => PR_BY_EX[p.exercise_id] = p);
    renderStats(xp, sessions, body);
    renderBodygraph(ranks, prs);
    renderRankList(ranks, prs);
    renderRecovery(recovery);
    renderQuickStart(sessions);
    renderCalendar(cal, sessions);
    renderWeeklyVolume(weekly);
    renderDeloadBanner(deload);
    renderRestDayPrompt(sessions, restDays);
  } catch (e) { console.error("dashboard load failed", e); toast("Could not load dashboard"); }
}

/* ── Deload banner (dismissible, per ISO week) ── */
function renderDeloadBanner(deload) {
  const banner = $("#deload-banner");
  if (!banner) return;
  const dismissedWeek = localStorage.getItem(LS_DELOAD);
  const thisWeek = isoWeekKey(new Date());
  if (deload && deload.deload_recommended && dismissedWeek !== thisWeek) {
    banner.hidden = false;
    $("#deload-dismiss").onclick = () => { localStorage.setItem(LS_DELOAD, thisWeek); banner.hidden = true; };
  } else {
    banner.hidden = true;
  }
}

/* ── Rest-day prompt (only when nothing logged today) ── */
function renderRestDayPrompt(sessions, restDays) {
  const prompt = $("#rest-day-prompt");
  if (!prompt) return;
  const todayStr = new Date().toISOString().slice(0, 10);
  const workedToday = (sessions || []).some(s => String(s.date).slice(0, 10) === todayStr);
  const restedToday = (restDays || []).some(d => String(d).slice(0, 10) === todayStr);
  if (workedToday || restedToday) { prompt.hidden = true; return; }
  prompt.hidden = false;
  $("#mark-rest-day").onclick = async () => {
    try {
      await apiPost(`${API}/rest-day`, {});
      prompt.hidden = true;
      toast("🌙 Rest day logged — streak kept alive");
      loadDashboard();
    } catch (e) { toast("Could not mark rest day"); }
  };
}

function startOfWeekCount(sessions) {
  const now = new Date(); const day = (now.getDay() + 6) % 7; // Mon=0
  const monday = new Date(now); monday.setDate(now.getDate() - day); monday.setHours(0,0,0,0);
  return sessions.filter(s => new Date(s.date + "T00:00:00") >= monday).length;
}
function renderStats(xp, sessions, body) {
  const grid = $("#gym-stats");
  const bw = (body && body[0]) ? body[0].weight_kg : null;
  const rank = xp.overall_rank || "Bronze";
  const cards = [
    { v: xp.streak_days || 0, l: "Day Streak", sub: "🔥 keep it alive", gold: true },
    { v: (xp.total_xp || 0).toLocaleString(), l: "Total XP", sub: `${RANK_ICON[rank]||""} ${rank}` },
    { v: bw != null ? fmtKg(bw) + "kg" : "—", l: "Bodyweight", sub: bw != null ? "latest" : "log in Progress" },
    { v: startOfWeekCount(sessions), l: "This Week", sub: "sessions" },
  ];
  grid.innerHTML = "";
  cards.forEach(c => {
    const card = el("div", "stat-card sci-fi-panel");
    card.innerHTML = `<div class="stat-value ${c.gold ? "gold" : ""}">${c.v}</div>
      <div class="stat-label">${c.l}</div><div class="stat-sub">${esc(c.sub)}</div>`;
    corners(card); grid.appendChild(card);
  });
}

/* Best lift per muscle group from PRs (for tooltip + rank list) */
function bestLiftByMuscle(prs) {
  const map = {};
  prs.forEach(p => {
    const m = p.muscle_group;
    if (!map[m] || (p.one_rep_max || 0) > (map[m].one_rep_max || 0)) map[m] = p;
  });
  return map;
}

function renderRankList(ranks, prs) {
  const wrap = $("#rank-list"); wrap.innerHTML = "";
  const best = bestLiftByMuscle(prs);
  const rankMap = {}; ranks.forEach(r => rankMap[r.muscle_group] = r);
  // union of all muscle groups we know about
  const groups = new Set([...ranks.map(r => r.muscle_group), ...Object.keys(best)]);
  const rows = Array.from(groups).map(g => ({
    group: g, rank: (rankMap[g] && rankMap[g].current_rank) || "Bronze",
    ranked: !!rankMap[g], best: best[g],
  }));
  rows.sort((a, b) => (RANK_ORDER.indexOf(b.rank) - RANK_ORDER.indexOf(a.rank))
    || ((b.best?.one_rep_max || 0) - (a.best?.one_rep_max || 0)));
  if (!rows.length) { wrap.innerHTML = `<div class="empty-note">No lifts logged yet — start a workout!</div>`; return; }
  rows.forEach(r => {
    const pct = ((RANK_ORDER.indexOf(r.rank) + 1) / RANK_ORDER.length) * 100;
    const color = r.ranked ? RANK_COLORS[r.rank] : "#3a4a5a";
    const liftTxt = r.best ? `${esc(r.best.exercise_name)} ${fmtKg(r.best.weight_kg)}kg` : "—";
    const row = el("div", "rank-list-row");
    row.innerHTML = `<div class="rl-top"><span class="rl-name">${esc(r.group)}</span>
      <span class="rl-rank" style="color:${color}">${RANK_ICON[r.rank]||""} ${r.ranked ? r.rank : "Unranked"}</span></div>
      <div class="rl-bar"><span style="width:${r.ranked?pct:6}%;background:${color}"></span></div>
      <div class="muted-sub" style="margin-top:3px">${liftTxt}</div>`;
    wrap.appendChild(row);
  });
}

function renderRecovery(recovery) {
  const wrap = $("#muscle-recovery"); wrap.innerHTML = "";
  if (!recovery.length) { wrap.innerHTML = `<div class="empty-note">No training data yet.</div>`; return; }
  recovery.forEach(r => {
    let cls = "rec-never", label = "Untrained";
    if (r.days_since != null) {
      if (r.days_since <= 1) { cls = "rec-recovering"; label = "Recovering"; }
      else if (r.days_since <= 3) { cls = "rec-ready"; label = "Ready"; }
      else { cls = "rec-overdue"; label = "Overdue"; }
    }
    const days = r.days_since == null ? "—" : (r.days_since === 0 ? "today" : `${r.days_since}d ago`);
    const row = el("div", "recovery-row");
    row.innerHTML = `<span class="rec-name">${esc(r.muscle_group)} <small class="muted-sub">${days}</small></span>
      <span class="rec-status ${cls}">${label}</span>`;
    wrap.appendChild(row);
  });
}

function suggestNextDayType(sessions) {
  const last = sessions.find(s => s.day_type && ROTATION.includes(s.day_type));
  if (!last) return "push";
  const idx = ROTATION.indexOf(last.day_type);
  return ROTATION[(idx + 1) % ROTATION.length];
}
function renderQuickStart(sessions) {
  const wrap = $("#quick-start"); wrap.innerHTML = "";
  const nextType = suggestNextDayType(sessions);
  ROUTINES.forEach(r => {
    const up = r.day_type === nextType;
    const b = el("button", "qs-btn" + (up ? " up-next" : ""));
    b.innerHTML = `<span>${esc(r.name)}<br><span class="qs-meta">${esc(r.description||"")}</span></span>
      ${up ? '<span class="qs-badge">⭐ UP NEXT</span>' : '<span class="qs-meta">Start ▶</span>'}`;
    b.addEventListener("click", () => { switchTab("workout"); startRoutine(r.id); });
    wrap.appendChild(b);
  });
}

function renderCalendar(cal, sessions) {
  const wrap = $("#consistency-calendar"); wrap.innerHTML = "";
  const byDate = {}; sessions.forEach(s => byDate[s.date] = s.routine_name || "Workout");
  const today = new Date(); today.setHours(0,0,0,0);
  const todayStr = today.toISOString().slice(0, 10);
  // 12 weeks ending this week; align to Monday
  const day = (today.getDay() + 6) % 7;
  const thisMon = new Date(today); thisMon.setDate(today.getDate() - day);
  const start = new Date(thisMon); start.setDate(thisMon.getDate() - 11 * 7);
  for (let w = 0; w < 12; w++) {
    const col = el("div", "cc-week");
    for (let d = 0; d < 7; d++) {
      const dt = new Date(start); dt.setDate(start.getDate() + w * 7 + d);
      const key = dt.toISOString().slice(0, 10);
      const dot = el("div", "cc-dot");
      const status = cal[key];
      if (key > todayStr) dot.classList.add("future");
      if (status === "workout" || byDate[key]) dot.classList.add("worked");
      else if (status === "rest") { dot.classList.add("rest"); dot.textContent = "🌙"; }
      if (key === todayStr) dot.classList.add("today");
      dot.title = `${key}${byDate[key] ? " · " + byDate[key] : (status === "workout" ? " · Workout" : (status === "rest" ? " · Rest day" : ""))}`;
      col.appendChild(dot);
    }
    wrap.appendChild(col);
  }
}

function renderWeeklyVolume(weekly) {
  killChart("weekly");
  const empty = $("#weekly-volume-empty");
  const data = (weekly || []).filter(w => w.current > 0);
  if (!data.length) { empty.hidden = false; return; }
  empty.hidden = true;
  const labels = data.map(w => {
    const c = w.change_pct;
    const tag = c == null ? "" : (c >= 0 ? `  ▲${c}%` : `  ▼${Math.abs(c)}%`);
    return w.muscle_group + tag;
  });
  const ctx = $("#weekly-volume-chart");
  CHARTS.weekly = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{
      data: data.map(w => w.current),
      backgroundColor: data.map(w => (w.change_pct == null || w.change_pct >= 0) ? "rgba(0,217,255,.55)" : "rgba(255,77,77,.5)"),
      borderColor: data.map(w => (w.change_pct == null || w.change_pct >= 0) ? CYAN : "#ff4d4d"),
      borderWidth: 1, borderRadius: 4,
    }] },
    options: { indexAxis: "y", responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => fmtKg(c.raw) + " kg" } } },
      scales: gridScales(true) },
  });
}

/* ══ 3. BODYGRAPH (body-highlighter, front + back) ═════════════════════════ */
function jumpToExercises(group) {
  exFilter = group.charAt(0).toUpperCase() + group.slice(1);
  switchTab("exercises");
  $$(".filter-pill").forEach(x => x.classList.toggle("active", x.dataset.f === exFilter));
  renderExGrid();
}

function renderBodygraph(ranks, prs) {
  const front = $("#bodygraph-front"), back = $("#bodygraph-back");
  if (!window.BodyHighlighter || !front || !back) return;
  const rankMap = {}; ranks.forEach(r => rankMap[r.muscle_group] = r);
  const best = bestLiftByMuscle(prs);
  // colour each library slug by the rank of the group it belongs to
  const colors = {};
  Object.entries(MUSCLE_TO_SLUGS).forEach(([group, slugs]) => {
    const rk = rankMap[group];
    const color = rk ? (RANK_COLORS[rk.current_rank] || UNRANKED) : UNRANKED;
    slugs.forEach(s => colors[s] = color);
  });
  const onClick = (slug) => { const g = SLUG_TO_MUSCLE[slug]; if (g) jumpToExercises(g); };
  BodyHighlighter.render(front, { view: "anterior",  colors, defaultColor: UNRANKED, onClick });
  BodyHighlighter.render(back,  { view: "posterior", colors, defaultColor: UNRANKED, onClick });
  attachBodygraphTooltips([front, back], rankMap, best);
}

function attachBodygraphTooltips(holders, rankMap, best) {
  const tooltip = $("#bodygraph-tooltip");
  holders.forEach(holder => {
    $$(".bh-muscle", holder).forEach(poly => {
      const slug = poly.getAttribute("data-muscle");
      const group = SLUG_TO_MUSCLE[slug];
      poly.addEventListener("mousemove", (e) => {
        if (!group) { tooltip.hidden = true; return; }
        const rk = rankMap[group]; const rank = rk ? rk.current_rank : null;
        const b = best[group];
        const liftTxt = b ? `${b.exercise_name} — ${fmtKg(b.weight_kg)}kg × ${b.reps}` : "no lift yet";
        const color = rank ? RANK_COLORS[rank] : "#8aa";
        tooltip.innerHTML = `<div class="tt-rank" style="color:${color}">${esc(group)} · ${rank ? (RANK_ICON[rank]+" "+rank) : "Unranked"}</div>
          <div class="tt-lift">${esc(liftTxt)} · tap to see exercises</div>`;
        tooltip.hidden = false;
        const pad = 14; let x = e.clientX + pad, y = e.clientY + pad;
        if (x + 200 > window.innerWidth) x = e.clientX - 200 - pad;
        tooltip.style.left = x + "px"; tooltip.style.top = y + "px";
      });
      poly.addEventListener("mouseleave", () => { tooltip.hidden = true; });
    });
  });
}


/* ══ 4. WORKOUT ENGINE ════════════════════════════════════════════════════ */
const LS_KEY = "gym_active_session";
let S = null;          // active session state object
let CURRENT_EX_ID = null;   // last exercise interacted with (AI-trainer context)

function saveLS() { if (S) localStorage.setItem(LS_KEY, JSON.stringify(S)); else localStorage.removeItem(LS_KEY); }
function clearSession() { S = null; localStorage.removeItem(LS_KEY); stopRestTimer(); stopSessionTimer(); }

async function loadWorkout() {
  // If a session is live in memory, keep showing it.
  if (S) { renderActiveSession(); return; }
  renderRoutinePicker();
  // detect resumable session (server truth)
  try {
    const active = await apiGet(`${API}/sessions/active`);
    if (active && active.id) {
      const banner = $("#resume-banner");
      $("#resume-name").textContent = active.routine_name || "workout";
      const started = active.start_time ? new Date(active.start_time) : null;
      $("#resume-started").textContent = started ? `(started ${started.toTimeString().slice(0,5)})` : "";
      banner.hidden = false;
      $("#resume-yes").onclick = () => resumeSession(active);
      $("#resume-discard").onclick = async () => {
        banner.hidden = true;
        try { await apiDel(`${API}/sessions/${active.id}`); } catch (e) {}
        localStorage.removeItem(LS_KEY);
        toast("Session discarded");
      };
    } else {
      $("#resume-banner").hidden = true;
      localStorage.removeItem(LS_KEY);
    }
  } catch (e) { /* ignore */ }
}

function renderRoutinePicker() {
  const wrap = $("#workout-routines"); wrap.hidden = false; $("#active-session").hidden = true;
  wrap.innerHTML = "";
  // suggested routine uses recent sessions
  apiGet(`${API}/sessions?limit=10`).then(sessions => {
    const nextType = suggestNextDayType(sessions);
    ROUTINES.forEach(r => {
      getRoutineFull(r.id).then(full => {
        const exCount = (full.exercises || []).length;
        const targets = loadTargets();
        const target = targets[r.id] || estimateRoutineMinutes(full);
        const up = r.day_type === nextType;
        const card = el("div", "routine-card sci-fi-panel" + (up ? " up-next" : ""));
        card.innerHTML = `${up ? '<span class="rc-badge">⭐ UP NEXT</span>' : ""}
          <div class="rc-name">${esc(r.name)}</div>
          <div class="rc-desc">${esc(r.description || "")}</div>
          <div class="rc-meta"><span><b>${exCount}</b> exercises</span>
            <span class="rc-duration" data-rid="${r.id}">target
              <button class="dur-step rc-dur-minus" aria-label="Decrease">−</button>
              <b class="rc-dur-val">${target}</b><span class="dur-unit">min</span>
              <button class="dur-step rc-dur-plus" aria-label="Increase">+</button>
            </span></div>`;
        card.addEventListener("click", () => startRoutine(r.id));
        // duration steppers must not start the workout
        const dwrap = card.querySelector(".rc-duration");
        const valEl = dwrap.querySelector(".rc-dur-val");
        const bump = (delta, e) => {
          e.stopPropagation();
          let v = Math.max(5, Math.min(240, (parseInt(valEl.textContent, 10) || target) + delta));
          valEl.textContent = v; saveTarget(r.id, v);
        };
        dwrap.querySelector(".rc-dur-minus").addEventListener("click", (e) => bump(-5, e));
        dwrap.querySelector(".rc-dur-plus").addEventListener("click", (e) => bump(5, e));
        corners(card); wrap.appendChild(card);
      });
    });
  });
}

function estimateRoutineMinutes(full) {
  const totalSets = (full.exercises || []).reduce((a, e) => a + (e.sets || 0), 0);
  return Math.max(5, Math.round(totalSets * 2.5));
}

async function startRoutine(routineId) {
  if (S) { toast("Finish your current session first"); switchTab("workout"); return; }
  initAudio();
  requestNotifyPermission();
  const routine = await getRoutineFull(routineId);
  const startTime = new Date().toISOString();
  const res = await apiPost(`${API}/sessions/start`, { routine_id: routineId, start_time: startTime });
  const targets = loadTargets();
  const target = targets[routineId] || estimateRoutineMinutes(routine);
  S = {
    id: res.session_id, routineId, routineName: routine.name, dayType: routine.day_type,
    startTime, targetDuration: target, exercises: routine.exercises.map(rex => buildExState(rex)),
  };
  saveLS();
  switchTab("workout");
  renderActiveSession();
  // fetch last-session data per exercise (async, fills ghosts + overload hints)
  S.exercises.forEach(ex => hydrateLastSession(ex));
}

function buildExState(rex) {
  return {
    routineExerciseId: rex.routine_exercise_id, exerciseId: rex.exercise_id,
    name: rex.name, muscle_group: rex.muscle_group, equipment: rex.equipment,
    exercise_type: rex.exercise_type, is_cardio: !!rex.is_cardio,
    rep_min: rex.rep_min, rep_max: rex.rep_max, rest_seconds: rex.rest_seconds || 90,
    plannedSets: rex.sets || 3, rowCount: rex.sets || 3,
    lastSession: undefined, loggedSets: [],
    ranks: { bronze: rex.rank_bronze, silver: rex.rank_silver, gold: rex.rank_gold, platinum: rex.rank_platinum, diamond: rex.rank_diamond },
  };
}

async function hydrateLastSession(ex) {
  try {
    const ls = await apiGet(`${API}/exercises/${ex.exerciseId}/last-session`);
    ex.lastSession = (ls && ls.sets) ? ls : null;
    saveLS();
    const card = $(`.exercise-card[data-ex="${ex.exerciseId}"]`);
    if (card) refreshExerciseCard(ex);
  } catch (e) { ex.lastSession = null; }
}

async function resumeSession(active) {
  $("#resume-banner").hidden = true;
  const routine = await getRoutineFull(active.routine_id);
  const targets = loadTargets();
  S = {
    id: active.id, routineId: active.routine_id, routineName: active.routine_name || routine.name,
    dayType: active.day_type || routine.day_type, startTime: active.start_time,
    targetDuration: targets[active.routine_id] || estimateRoutineMinutes(routine),
    exercises: routine.exercises.map(rex => buildExState(rex)),
  };
  // merge already-logged sets from server
  const byEx = {};
  (active.sets || []).forEach(st => { (byEx[st.exercise_id] = byEx[st.exercise_id] || []).push(st); });
  S.exercises.forEach(ex => {
    const logged = byEx[ex.exerciseId] || [];
    ex.loggedSets = logged.map(st => ({
      setId: st.id, setNumber: st.set_number, setType: st.set_type,
      weight: st.weight_kg, reps: st.reps, isPr: st.is_pr, oneRm: oneRm(st.weight_kg, st.reps),
    }));
    if (ex.loggedSets.length > ex.rowCount) ex.rowCount = ex.loggedSets.length;
    delete byEx[ex.exerciseId];
  });
  // any sets for exercises not in routine (swapped) — append as extra cards
  Object.keys(byEx).forEach(exId => {
    const lib = EX_BY_ID[exId]; if (!lib) return;
    const st = buildExState({ routine_exercise_id: null, exercise_id: +exId, name: lib.name,
      muscle_group: lib.muscle_group, equipment: lib.equipment, exercise_type: lib.exercise_type,
      is_cardio: lib.exercise_type === "cardio", rep_min: 8, rep_max: 12, rest_seconds: 90, sets: byEx[exId].length,
      rank_bronze: lib.rank_bronze, rank_silver: lib.rank_silver, rank_gold: lib.rank_gold,
      rank_platinum: lib.rank_platinum, rank_diamond: lib.rank_diamond });
    st.loggedSets = byEx[exId].map(x => ({ setId: x.id, setNumber: x.set_number, setType: x.set_type,
      weight: x.weight_kg, reps: x.reps, isPr: x.is_pr, oneRm: oneRm(x.weight_kg, x.reps) }));
    S.exercises.push(st);
  });
  saveLS();
  initAudio(); requestNotifyPermission();
  renderActiveSession();
  S.exercises.forEach(ex => hydrateLastSession(ex));
  toast("Session resumed");
}

function renderActiveSession() {
  $("#workout-routines").hidden = true;
  $("#resume-banner").hidden = true;
  const wrap = $("#active-session"); wrap.hidden = false;
  $("#sh-routine").textContent = S.routineName;
  const durInput = $("#sh-dur-input");
  if (durInput) durInput.value = S.targetDuration || estimateRoutineMinutes({ exercises: S.exercises.map(e => ({ sets: e.plannedSets })) });
  const list = $("#exercise-list"); list.innerHTML = "";
  S.exercises.forEach(ex => list.appendChild(buildExerciseCard(ex)));
  updateSessionTotals();
  startSessionTimer();
}

/* ── Session target-duration stepper (header) ── */
function adjustSessionTarget(delta) {
  const input = $("#sh-dur-input"); if (!input) return;
  let v = parseInt(input.value, 10); if (isNaN(v)) v = 60;
  v = Math.max(5, Math.min(240, v + delta));
  input.value = v; commitSessionTarget();
}
function commitSessionTarget() {
  const input = $("#sh-dur-input"); if (!input || !S) return;
  let v = parseInt(input.value, 10); if (isNaN(v) || v < 5) v = 5; if (v > 240) v = 240;
  input.value = v; S.targetDuration = v;
  if (S.routineId != null) saveTarget(S.routineId, v);
  saveLS();
}
(function wireSessionTarget() {
  const minus = $("#sh-dur-minus"), plus = $("#sh-dur-plus"), input = $("#sh-dur-input");
  if (minus) minus.addEventListener("click", () => adjustSessionTarget(-5));
  if (plus)  plus.addEventListener("click", () => adjustSessionTarget(5));
  if (input) input.addEventListener("change", commitSessionTarget);
})();

/* ── Exercise card ── */
function overloadHint(ex) {
  if (!ex.lastSession || !ex.lastSession.sets) return false;
  const working = ex.lastSession.sets.filter(s => (s.set_type || "working") === "working");
  if (working.length < 2) return false;
  return working.every(s => (s.reps || 0) >= ex.rep_max);
}
function buildExerciseCard(ex) {
  const card = el("div", "exercise-card sci-fi-panel"); card.dataset.ex = ex.exerciseId;
  const cardio = isCardioEx(ex);
  const addLabel = cardio ? "+ Add Bout" : "+ Add Set";
  card.innerHTML = `
    <div class="ec-head">
      <div class="ec-title">
        <div class="ec-name">${esc(ex.name)}
          ${cardio ? "" : '<button class="ec-icon-btn warmup-btn" title="Warmup calculator">🔥</button>'}
        </div>
        <div class="ec-muscle">${esc(ex.muscle_group)} · ${esc(ex.equipment||(cardio?"cardio":""))}</div>
      </div>
      <div class="ec-actions">
        ${cardio ? "" : '<button class="ec-icon-btn swap-btn" title="Swap exercise">⇄</button>'}
        <button class="ec-icon-btn watch-btn" title="Watch">▶</button>
        <button class="ec-icon-btn remove-ex-btn" title="Remove from session">✕</button>
      </div>
    </div>
    <div class="ec-rankline"></div>
    <div class="ec-lasttime-slot"></div>
    <div class="ec-overload-slot"></div>
    <div class="set-rows"></div>
    <button class="btn add-set-btn">${addLabel}</button>`;
  corners(card);
  // wire header buttons
  const warmupBtn = card.querySelector(".warmup-btn");
  if (warmupBtn) warmupBtn.addEventListener("click", (e) => openWarmup(e.currentTarget, ex));
  const swapBtn = card.querySelector(".swap-btn");
  if (swapBtn) swapBtn.addEventListener("click", () => openSwap(ex));
  card.querySelector(".watch-btn").addEventListener("click", () => openVideo(EX_BY_ID[ex.exerciseId] || ex));
  card.querySelector(".remove-ex-btn").addEventListener("click", () => removeExercise(ex));
  card.querySelector(".add-set-btn").addEventListener("click", () => { ex.rowCount++; renderSetRows(ex, card); });
  renderSetRows(ex, card);
  if (ex._pr === undefined) ex._pr = PR_BY_EX[ex.exerciseId] || null;
  refreshExtras(ex, card);
  return card;
}

/* ── Remove exercise from the current session (session-only) ── */
async function removeExercise(ex) {
  if (!S) return;
  const n = ex.loggedSets.length;
  const msg = n
    ? `Remove ${ex.name} from this session? Its ${n} logged set${n > 1 ? "s" : ""} will be deleted. Sets for other exercises are kept.`
    : `Remove ${ex.name} from this session?`;
  if (!confirm(msg)) return;
  // delete this exercise's logged sets from the server (others untouched)
  for (const s of ex.loggedSets) {
    if (s.setId) { try { await apiDel(`${API}/sets/${s.setId}`); } catch (e) {} }
  }
  const idx = S.exercises.indexOf(ex);
  if (idx >= 0) S.exercises.splice(idx, 1);
  saveLS();
  renderActiveSession();
  updateSessionTotals();
  toast(`Removed ${ex.name}`);
}

/* ── Add an exercise to the current session (session-only, not the template) ── */
function addExerciseToSession(lib) {
  if (!S) return;
  if (S.exercises.some(e => e.exerciseId === lib.id)) { toast("Already in this session"); return; }
  const st = buildExState({
    routine_exercise_id: null, exercise_id: lib.id, name: lib.name,
    muscle_group: lib.muscle_group, equipment: lib.equipment,
    exercise_type: lib.exercise_type, is_cardio: lib.exercise_type === "cardio",
    rep_min: 8, rep_max: 12, rest_seconds: 90, sets: isCardioEx(lib) ? 1 : 3,
    rank_bronze: lib.rank_bronze, rank_silver: lib.rank_silver, rank_gold: lib.rank_gold,
    rank_platinum: lib.rank_platinum, rank_diamond: lib.rank_diamond,
  });
  S.exercises.push(st);
  saveLS();
  renderActiveSession();
  hydrateLastSession(st);
  const card = $(`.exercise-card[data-ex="${lib.id}"]`);
  if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  toast(`Added ${lib.name}`);
}
function refreshExerciseCard(ex) {
  const card = $(`.exercise-card[data-ex="${ex.exerciseId}"]`); if (card) refreshExtras(ex, card);
}
function refreshExtras(ex, card) {
  const cardio = isCardioEx(ex);
  // rank + PR line (cardio has no rank/PR — show a cardio tag instead)
  const rl = card.querySelector(".ec-rankline");
  if (cardio) {
    rl.innerHTML = `<span class="rank-badge rank-cardio">🏃 Cardio · +50 XP</span>`;
  } else if (ex._pr && ex._pr.weight_kg != null) {
    const lib = EX_BY_ID[ex.exerciseId] || {};
    const rank = computeRank(lib, ex._pr.weight_kg);
    rl.innerHTML = `<span class="rank-badge rank-${rank.toLowerCase()}">${RANK_ICON[rank]} ${rank}</span>
      <span class="muted-sub" style="margin-left:6px">PR ${fmtKg(ex._pr.weight_kg)}kg × ${ex._pr.reps}</span>`;
  } else { rl.innerHTML = `<span class="rank-badge rank-unranked">Unranked</span>`; }
  // last time
  const slot = card.querySelector(".ec-lasttime-slot");
  if (ex.lastSession && ex.lastSession.sets && ex.lastSession.sets.length) {
    const sets = cardio
      ? ex.lastSession.sets.map(s => `${s.reps} min${s.notes ? " (" + esc(s.notes) + ")" : ""}`).join(", ")
      : ex.lastSession.sets.map(s => `${fmtKg(s.weight_kg)}kg×${s.reps}`).join(", ");
    const d = ex.lastSession.date ? new Date(ex.lastSession.date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
    slot.innerHTML = `<div class="ec-lasttime"><span class="lt-label">LAST</span>${sets} <span class="muted-sub">(${esc(d)})</span></div>`;
  } else if (ex.lastSession === null) { slot.innerHTML = ""; }
  // overload (not for cardio)
  const os = card.querySelector(".ec-overload-slot");
  os.innerHTML = (!cardio && overloadHint(ex)) ? `<div class="ec-overload">📈 Add 2.5kg today — you maxed the rep range last time</div>` : "";
  renderSetRows(ex, card);
}

function renderSetRows(ex, card) {
  if (isCardioEx(ex)) { renderCardioRows(ex, card); return; }
  const wrap = card.querySelector(".set-rows"); wrap.innerHTML = "";
  const barbell = (ex.equipment === "barbell");
  const lastSets = (ex.lastSession && ex.lastSession.sets) ? ex.lastSession.sets : [];
  const rows = Math.max(ex.rowCount, ex.loggedSets.length);
  for (let i = 0; i < rows; i++) {
    const logged = ex.loggedSets[i];
    const ghost = lastSets[i];
    const row = el("div", "set-row" + (logged ? " done" : "")); row.dataset.idx = i;
    const defType = "working";
    const typeOpts = ["warmup", "working", "dropset", "failure"].map(t =>
      `<option value="${t}" ${((logged?logged.setType:defType) === t) ? "selected" : ""}>${t[0].toUpperCase()+t.slice(1)}</option>`).join("");
    const wGhost = ghost ? `placeholder="${fmtKg(ghost.weight_kg)}"` : `placeholder="kg"`;
    const rGhost = ghost ? `placeholder="${ghost.reps}"` : `placeholder="reps"`;
    row.innerHTML = `
      <div class="set-num">${i + 1}</div>
      <select class="set-type-sel" ${logged ? "disabled" : ""}>${typeOpts}</select>
      <div class="num-wrap">
        <input class="num-input w-in" inputmode="decimal" ${wGhost} value="${logged ? fmtKg(logged.weight) : ""}" ${logged ? "disabled" : ""}>
        ${barbell ? '<button class="plate-btn" title="Plate calculator" tabindex="-1">🏋️</button>' : ""}
      </div>
      <input class="num-input r-in" inputmode="numeric" ${rGhost} value="${logged ? logged.reps : ""}" ${logged ? "disabled" : ""}>
      <button class="set-check" title="${logged ? "Delete set" : "Complete set"}">${logged ? "🗑" : "✓"}</button>`;
    const check = row.querySelector(".set-check");
    if (logged) {
      check.addEventListener("click", () => deleteLoggedSet(ex, i, card));
    } else {
      check.addEventListener("click", () => completeSet(ex, i, row, card));
      if (ghost) {
        const fill = el("button", "ec-icon-btn", "↻"); fill.title = "Fill last time";
        fill.style.cssText = "width:26px;height:26px;min-height:26px;position:absolute;right:2px;top:50%;transform:translateY(-50%);";
        check.insertAdjacentElement("beforebegin", fill);
        fill.addEventListener("click", () => {
          row.querySelector(".w-in").value = fmtKg(ghost.weight_kg);
          row.querySelector(".r-in").value = ghost.reps;
        });
      }
      const plateBtn = row.querySelector(".plate-btn");
      if (plateBtn) plateBtn.addEventListener("click", (e) => openPlate(e.currentTarget, +row.querySelector(".w-in").value || 0));
    }
    wrap.appendChild(row);
  }
}

/* Cardio rows: duration (min) + intensity/notes only — no weight/reps/type. */
function renderCardioRows(ex, card) {
  const wrap = card.querySelector(".set-rows"); wrap.innerHTML = "";
  const lastSets = (ex.lastSession && ex.lastSession.sets) ? ex.lastSession.sets : [];
  const rows = Math.max(ex.rowCount, ex.loggedSets.length, 1);
  for (let i = 0; i < rows; i++) {
    const logged = ex.loggedSets[i];
    const ghost = lastSets[i];
    const row = el("div", "set-row cardio-row" + (logged ? " done" : "")); row.dataset.idx = i;
    const durVal = logged ? logged.reps : "";
    const durGhost = ghost ? `placeholder="${ghost.reps}"` : `placeholder="${CARDIO_DEFAULT_MIN}"`;
    const intVal = logged ? (logged.notes || "") : "";
    const intGhost = (ghost && ghost.notes) ? `placeholder="${esc(ghost.notes)}"` : `placeholder="speed / incline e.g. 3.5 / 13"`;
    row.innerHTML = `
      <div class="set-num">${i + 1}</div>
      <div class="cardio-dur"><input class="num-input dur-min" inputmode="numeric" ${durGhost} value="${durVal}" ${logged ? "disabled" : ""}><span class="dur-unit">min</span></div>
      <input class="num-input int-in" type="text" ${intGhost} value="${esc(intVal)}" ${logged ? "disabled" : ""}>
      <button class="set-check" title="${logged ? "Delete" : "Log cardio"}">${logged ? "🗑" : "✓"}</button>`;
    const check = row.querySelector(".set-check");
    if (logged) {
      check.addEventListener("click", () => deleteLoggedSet(ex, i, card));
    } else {
      // default the duration to 30 min if left blank on focus-out convenience
      const durEl = row.querySelector(".dur-min");
      if (!durEl.value && !ghost) durEl.value = CARDIO_DEFAULT_MIN;
      check.addEventListener("click", () => completeSet(ex, i, row, card));
    }
    wrap.appendChild(row);
  }
}

async function completeSet(ex, idx, row, card) {
  const cardio = isCardioEx(ex);
  const setNumber = idx + 1;
  let weight, reps, type, notes = "";
  if (cardio) {
    reps = parseInt(row.querySelector(".dur-min").value, 10);
    if (isNaN(reps) || reps <= 0) reps = CARDIO_DEFAULT_MIN;
    weight = 0; type = "working";
    notes = (row.querySelector(".int-in").value || "").trim();
  } else {
    const w = parseFloat(row.querySelector(".w-in").value);
    reps = parseInt(row.querySelector(".r-in").value, 10);
    type = row.querySelector(".set-type-sel").value;
    if (isNaN(reps) || reps <= 0) { toast("Enter reps"); return; }
    weight = isNaN(w) ? 0 : w;
  }
  CURRENT_EX_ID = ex.exerciseId;
  try {
    const res = await apiPost(`${API}/sets`, { session_id: S.id, exercise_id: ex.exerciseId,
      set_number: setNumber, set_type: type, weight_kg: weight, reps, notes });
    ex.loggedSets[idx] = { setId: res.id, setNumber, setType: type, weight, reps, notes,
      isPr: res.is_pr, oneRm: res.one_rep_max };
    saveLS();
    row.classList.add("done");
    if (window.SoundFX) try { SoundFX.play && SoundFX.play("confirm"); } catch(e) {}
    if (res.is_pr && !cardio) {
      ex._pr = { weight_kg: weight, reps, one_rep_max: res.one_rep_max };
      firePR(ex, weight, reps, res.one_rep_max);
    }
    refreshExtras(ex, card);
    updateSessionTotals();
    // rest timer for non-cardio working-ish sets only
    if (!cardio && type !== "warmup") startRestTimer(ex, setNumber + 1);
  } catch (e) { console.error(e); toast("Could not log set"); }
}

async function deleteLoggedSet(ex, idx, card) {
  const logged = ex.loggedSets[idx]; if (!logged) return;
  const desc = isCardioEx(ex) ? `${logged.reps} min` : `${fmtKg(logged.weight)}kg × ${logged.reps}`;
  if (!confirm(`Delete set ${idx + 1}? (${desc})`)) return;
  try {
    if (logged.setId) await apiDel(`${API}/sets/${logged.setId}`);
    ex.loggedSets.splice(idx, 1);
    if (ex.rowCount > ex.plannedSets && ex.rowCount > ex.loggedSets.length) ex.rowCount--;
    saveLS();
    refreshExtras(ex, card);
    updateSessionTotals();
    toast("Set deleted");
  } catch (e) { toast("Could not delete set"); }
}

function updateSessionTotals() {
  let vol = 0, sets = 0;
  S.exercises.forEach(ex => ex.loggedSets.forEach(s => { vol += (s.weight || 0) * (s.reps || 0); sets++; }));
  $("#sh-volume").textContent = Math.round(vol).toLocaleString();
  $("#sh-sets").textContent = sets;
}

/* ── Session timer (timestamp-based, survives refresh/background) ── */
let sessionTimerId = null;
function startSessionTimer() {
  stopSessionTimer();
  const tick = () => {
    const ms = Date.now() - new Date(S.startTime).getTime();
    const s = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    $("#sh-timer").textContent = (h > 0 ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
  };
  tick(); sessionTimerId = setInterval(tick, 1000);
}
function stopSessionTimer() { if (sessionTimerId) { clearInterval(sessionTimerId); sessionTimerId = null; } }

/* ── Rest timer (timestamp-based) ── */
let restTimerId = null, restEndTime = 0, restFired = false, restCtx = null;
const RT_CIRC = 2 * Math.PI * 52;
function startRestTimer(ex, nextSetNo) {
  const dur = ex.rest_seconds || 90;
  restEndTime = Date.now() + dur * 1000; restFired = false;
  restCtx = { total: dur, label: `Set ${nextSetNo} · ${ex.name}` };
  const panel = $("#rest-timer"); panel.hidden = false;
  document.body.classList.add("rest-active");   // adds bottom padding so ✓ buttons clear the timer
  $("#rt-label").textContent = restCtx.label;
  $("#rt-progress").style.strokeDasharray = RT_CIRC;
  stopRestTimerInterval();
  const tick = () => {
    const remain = Math.max(0, restEndTime - Date.now());
    const secs = Math.ceil(remain / 1000);
    $("#rt-count").textContent = Math.floor(secs / 60) + ":" + String(secs % 60).padStart(2, "0");
    const frac = restCtx.total > 0 ? (remain / 1000) / restCtx.total : 0;
    $("#rt-progress").style.strokeDashoffset = RT_CIRC * (1 - frac);
    if (remain <= 0 && !restFired) { restFired = true; onRestOver(); }
  };
  tick(); restTimerId = setInterval(tick, 250);
}
function onRestOver() {
  restBeep();
  notify("Rest over", restCtx ? restCtx.label : "Next set");
  const panel = $("#rest-timer");
  $("#rt-count").textContent = "0:00";
  setTimeout(() => { if (restFired) stopRestTimer(); }, 1500);
}
function stopRestTimerInterval() { if (restTimerId) { clearInterval(restTimerId); restTimerId = null; } }
function stopRestTimer() { stopRestTimerInterval(); const p = $("#rest-timer"); if (p) p.hidden = true; document.body.classList.remove("rest-active"); }
$("#rt-skip").addEventListener("click", stopRestTimer);
$("#rt-add").addEventListener("click", () => { restEndTime += 30000; if (restCtx) restCtx.total += 30; restFired = false; });

/* ── Notifications ── */
function requestNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") {
    try { Notification.requestPermission(); } catch (e) {}
  }
}
function notify(title, body) {
  if ("Notification" in window && Notification.permission === "granted") {
    try { new Notification(title, { body, silent: false }); } catch (e) {}
  }
}

/* ── PR celebration + confetti ── */
function firePR(ex, weight, reps, orm) {
  chime();
  const t = el("div", "pr-toast", `🏆 NEW PR — ${fmtKg(weight)}kg × ${reps} <span class="muted-sub">(est. 1RM ${fmtKg(orm)}kg)</span>`);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
  const card = $(`.exercise-card[data-ex="${ex.exerciseId}"]`); if (card) { card.classList.add("pulse-pr"); setTimeout(() => card.classList.remove("pulse-pr"), 1000); }
  confettiBurst();
}
function confettiBurst() {
  const canvas = $("#confetti-canvas"); const ctx = canvas.getContext("2d");
  canvas.width = window.innerWidth; canvas.height = window.innerHeight;
  const colors = ["#00d9ff", "#ffd700", "#7f77dd", "#ff006e", "#3FB950"];
  const parts = [];
  for (let i = 0; i < 64; i++) {
    parts.push({ x: canvas.width / 2, y: canvas.height / 3,
      vx: (Math.random() - 0.5) * 14, vy: Math.random() * -12 - 4,
      g: 0.35 + Math.random() * 0.2, s: 5 + Math.random() * 6, rot: Math.random() * 6.28,
      vr: (Math.random() - 0.5) * 0.4, color: colors[i % colors.length], life: 1 });
  }
  let frame = 0;
  (function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    let alive = false;
    parts.forEach(p => {
      p.vy += p.g; p.x += p.vx; p.y += p.vy; p.rot += p.vr; p.life -= 0.012;
      if (p.life > 0 && p.y < canvas.height + 20) {
        alive = true; ctx.save(); ctx.globalAlpha = Math.max(0, p.life);
        ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = p.color;
        ctx.fillRect(-p.s / 2, -p.s / 2, p.s, p.s * 0.6); ctx.restore();
      }
    });
    frame++;
    if (alive && frame < 200) requestAnimationFrame(draw);
    else ctx.clearRect(0, 0, canvas.width, canvas.height);
  })();
}

/* ── Warmup calculator ── */
let openPopover = null;
function closePopover() { if (openPopover) { openPopover.remove(); openPopover = null; document.removeEventListener("click", outsidePopover, true); } }
function outsidePopover(e) { if (openPopover && !openPopover.contains(e.target)) closePopover(); }
function placePopover(anchor, pop) {
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  let left = r.left, top = r.bottom + 6;
  const pw = pop.offsetWidth, ph = pop.offsetHeight;
  if (left + pw > window.innerWidth - 8) left = window.innerWidth - pw - 8;
  if (top + ph > window.innerHeight - 8) top = r.top - ph - 6;
  pop.style.left = Math.max(8, left) + "px"; pop.style.top = Math.max(8, top) + "px";
  openPopover = pop;
  setTimeout(() => document.addEventListener("click", outsidePopover, true), 0);
}
function openWarmup(anchor, ex) {
  closePopover();
  // base weight = first working weight entered, else last-session best, else 20
  const card = anchor.closest(".exercise-card");
  let base = 0;
  card.querySelectorAll(".set-row").forEach(r => { const w = parseFloat(r.querySelector(".w-in").value); if (!base && !isNaN(w) && w > 0) base = w; });
  if (!base && ex.lastSession && ex.lastSession.best) base = ex.lastSession.best.weight_kg;
  if (!base) base = 20;
  const steps = [["Bar", BAR_KG, 10], ["40%", round1(base * 0.4), 8], ["60%", round1(base * 0.6), 5], ["80%", round1(base * 0.8), 3]];
  const pop = el("div", "popover");
  pop.innerHTML = `<h4>🔥 Warmup for ${fmtKg(base)}kg</h4>` +
    steps.map(s => `<div class="pop-row"><span>${s[0]}</span><b>${fmtKg(s[1])}kg × ${s[2]}</b></div>`).join("") +
    `<button class="btn btn-primary" style="width:100%;margin-top:10px" id="add-warmup">Add as warmup sets</button>`;
  placePopover(anchor, pop);
  pop.querySelector("#add-warmup").addEventListener("click", () => {
    // insert warmup rows at the top (pre-filled), pushing existing rows down
    const pre = steps.map(s => ({ setType: "warmup", weight: s[1], reps: s[2], warm: true }));
    ex._pendingWarmups = pre;
    ex.rowCount += pre.length;
    // render then fill the first N rows as warmups
    renderSetRows(ex, card);
    const rows = card.querySelectorAll(".set-row");
    pre.forEach((p, i) => {
      const row = rows[i]; if (!row) return;
      row.querySelector(".set-type-sel").value = "warmup";
      row.querySelector(".w-in").value = fmtKg(p.weight);
      row.querySelector(".r-in").value = p.reps;
    });
    closePopover();
    toast("Warmup sets added");
  });
}

/* ── Plate calculator ── */
function platesFor(weight) {
  let perSide = (weight - BAR_KG) / 2; const out = [];
  if (perSide <= 0) return out;
  for (const p of PLATES) { while (perSide >= p - 1e-6) { out.push(p); perSide = round1(perSide - p); } }
  return out;
}
function openPlate(anchor, weight) {
  closePopover();
  const pop = el("div", "popover");
  if (!weight || weight <= BAR_KG) {
    pop.innerHTML = `<h4>🏋️ Plates</h4><div class="plate-bar-note">Enter a weight above the ${BAR_KG}kg bar.</div>`;
  } else {
    const plates = platesFor(weight);
    if (!plates.length) {
      pop.innerHTML = `<h4>🏋️ ${fmtKg(weight)}kg</h4><div class="plate-bar-note">Just the ${BAR_KG}kg bar.</div>`;
    } else {
      const maxP = Math.max(...plates);
      const visual = plates.map(p => {
        const h = 22 + (p / maxP) * 30, w = 8 + (p / 25) * 8;
        return `<div class="plate-disc" style="height:${h}px;width:${w}px;background:${PLATE_COLORS[p]||"#888"};color:${p===5?'#111':'#fff'}">${p}</div>`;
      }).join("");
      const counts = {}; plates.forEach(p => counts[p] = (counts[p] || 0) + 1);
      const txt = Object.keys(counts).map(Number).sort((a, b) => b - a)
        .map(p => counts[p] > 1 ? `${counts[p]}×${p}` : `${p}`).join(" + ");
      pop.innerHTML = `<h4>🏋️ ${fmtKg(weight)}kg</h4>
        <div class="plate-bar-note">${BAR_KG}kg bar</div>
        <div class="plate-visual">${visual}</div>
        <div class="plate-per-side">per side: ${txt}</div>`;
    }
  }
  placePopover(anchor, pop);
}

/* ── Exercise swap ── */
async function openSwap(ex) {
  const modal = $("#swap-modal"); const body = $("#swap-modal-body");
  body.innerHTML = `<div class="empty-note">Loading…</div>`;
  openModal("swap-modal");
  const opts = await apiGet(`${API}/exercises/muscle/${encodeURIComponent(ex.muscle_group)}`);
  body.innerHTML = "";
  opts.filter(o => o.id !== ex.exerciseId).forEach(o => {
    const b = el("button", "qs-btn"); b.innerHTML = `<span>${esc(o.name)}<br><span class="qs-meta">${esc(o.equipment||"")} · ${esc(o.exercise_type||"")}</span></span><span class="qs-meta">Swap ⇄</span>`;
    b.addEventListener("click", () => { swapExercise(ex, o); closeModal("swap-modal"); });
    body.appendChild(b);
  });
  if (!body.children.length) body.innerHTML = `<div class="empty-note">No alternatives for this muscle group.</div>`;
}
function swapExercise(ex, newLib) {
  // keep already-logged sets attached to old exercise as a separate finished block?
  // Spec: session keeps any already-logged sets for the old exercise. We do that
  // by leaving those sets in the DB; the card is replaced with the new exercise.
  ex.exerciseId = newLib.id; ex.name = newLib.name; ex.muscle_group = newLib.muscle_group;
  ex.equipment = newLib.equipment; ex.exercise_type = newLib.exercise_type;
  ex.is_cardio = newLib.exercise_type === "cardio";
  ex.loggedSets = []; ex.lastSession = undefined; ex._pr = null; ex.rowCount = ex.plannedSets;
  ex.ranks = { bronze: newLib.rank_bronze, silver: newLib.rank_silver, gold: newLib.rank_gold, platinum: newLib.rank_platinum, diamond: newLib.rank_diamond };
  saveLS();
  renderActiveSession();
  const swapped = S.exercises.find(e => e.exerciseId === newLib.id);
  if (swapped) hydrateLastSession(swapped);
  toast(`Swapped to ${newLib.name}`);
}

/* ── Finish workout ── */
$("#finish-workout").addEventListener("click", finishWorkout);
async function finishWorkout() {
  if (!S) return;
  const totalSets = S.exercises.reduce((a, ex) => a + ex.loggedSets.length, 0);
  if (totalSets === 0) {
    if (!confirm("No sets logged. Discard this session?")) return;
    try { await apiDel(`${API}/sessions/${S.id}`); } catch (e) {}
    clearSession(); loadWorkout(); return;
  }
  let res;
  try { res = await apiPost(`${API}/sessions/${S.id}/end`, { end_time: new Date().toISOString() }); }
  catch (e) { toast("Could not finish session"); return; }
  showSummary(res);
}

function showSummary(res) {
  const sid = S.id;
  const dur = res.duration_minutes || 0;
  const vol = res.total_volume_kg || 0;
  const sets = res.total_sets || 0;
  let avgEff = res.avg_efficiency;   // rolling routine average (may be null)
  // gather PRs + per-muscle volume from state
  const prs = []; const muscleVol = {}; let cardioSets = 0;
  S.exercises.forEach(ex => {
    const cardio = isCardioEx(ex);
    ex.loggedSets.forEach(s => {
      if (cardio) { cardioSets++; return; }
      muscleVol[ex.muscle_group] = (muscleVol[ex.muscle_group] || 0) + (s.weight || 0) * (s.reps || 0);
      if (s.isPr) prs.push({ name: ex.name, weight: s.weight, reps: s.reps, orm: s.oneRm });
    });
  });
  const xpSets = sets * 10, xpPr = prs.length * 50, xpCardio = cardioSets * 50, xpSession = 100, xpStreak = 25;
  const xpTotal = xpSets + xpPr + xpCardio + xpSession + xpStreak;
  const body = $("#summary-modal-body");
  const muscleRows = Object.keys(muscleVol).sort((a, b) => muscleVol[b] - muscleVol[a])
    .map(m => `<div class="xp-line"><span style="text-transform:capitalize">${esc(m)}</span><b>${Math.round(muscleVol[m]).toLocaleString()} kg</b></div>`).join("");
  body.innerHTML = `
    <div class="summary-hero"><div class="sh-big">💪 Workout Complete</div>
      <div class="muted-sub">${esc(S.routineName)} · streak ${res.streak || 0} 🔥</div></div>
    <div class="summary-stats">
      <div class="ss"><b>${Math.round(vol).toLocaleString()}</b><small>kg vol</small></div>
      <div class="ss"><b>${sets}</b><small>sets</small></div>
    </div>
    <div class="modal-section"><h4>Logged duration</h4>
      <div class="dur-override">
        <button class="dur-step" id="sum-dur-minus" aria-label="Decrease">−</button>
        <input type="number" id="sum-dur-input" class="dur-input" inputmode="numeric" min="1" max="480" step="1" value="${dur}">
        <span class="dur-unit">min</span>
        <button class="dur-step" id="sum-dur-plus" aria-label="Increase">+</button>
        <span class="muted-sub" style="margin-left:8px">actual elapsed — adjust before saving</span>
      </div>
    </div>
    <div class="modal-section" id="eff-section"><h4>Session efficiency</h4>
      <div id="eff-line" class="eff-line"></div>
    </div>
    ${prs.length ? `<div class="modal-section"><h4>Personal Records</h4>${prs.map(p => `<div class="pr-hit-row">🏆 ${esc(p.name)} — ${fmtKg(p.weight)}kg × ${p.reps} <span class="muted-sub">(1RM ${fmtKg(p.orm)}kg)</span></div>`).join("")}</div>` : ""}
    <div class="modal-section"><h4>XP Earned</h4>
      <div class="xp-breakdown">
        <div class="xp-line"><span>Sets (${sets} × 10)</span><b>+${xpSets}</b></div>
        ${prs.length ? `<div class="xp-line"><span>PRs (${prs.length} × 50)</span><b>+${xpPr}</b></div>` : ""}
        ${cardioSets ? `<div class="xp-line"><span>Cardio (${cardioSets} × 50)</span><b>+${xpCardio}</b></div>` : ""}
        <div class="xp-line"><span>Session complete</span><b>+${xpSession}</b></div>
        <div class="xp-line"><span>Streak bonus</span><b>+${xpStreak}</b></div>
        <div class="xp-line" style="border-top:1px solid rgba(255,255,255,.1);margin-top:6px;padding-top:6px"><span><b>Total</b></span><b>+${xpTotal}</b></div>
      </div>
    </div>
    <div class="modal-section"><h4>Volume by muscle</h4><div class="xp-breakdown">${muscleRows || '<div class="muted-sub">—</div>'}</div></div>
    <div class="modal-section"><h4>Notes</h4>
      <textarea class="summary-notes" id="summary-notes" placeholder="How did it feel? Any tweaks for next time…"></textarea></div>
    <button class="btn btn-finish" style="width:100%" id="summary-done">Done</button>`;
  openModal("summary-modal");

  const durInput = $("#sum-dur-input");
  const effSection = $("#eff-section");
  function renderEff() {
    const d = Math.max(1, parseInt(durInput.value, 10) || dur);
    if (!vol) { effSection.hidden = true; return; }   // cardio-only → no volume
    effSection.hidden = false;
    const eff = round1(vol / d);
    let cmp = "";
    if (avgEff != null && avgEff > 0) {
      const pct = Math.round((eff - avgEff) / avgEff * 100);
      const up = eff >= avgEff;
      cmp = `<span class="eff-cmp ${up ? "up" : "down"}">vs ${avgEff} kg/min avg — ${up ? "▲" : "▼"} ${Math.abs(pct)}%</span>`;
    } else {
      cmp = `<span class="muted-sub">no prior average for this routine</span>`;
    }
    $("#eff-line").innerHTML = `<span class="eff-val">📊 ${eff} kg/min</span> ${cmp}`;
  }
  renderEff();
  let effTimer = null;
  const bumpDur = (delta) => { durInput.value = Math.max(1, Math.min(480, (parseInt(durInput.value, 10) || dur) + delta)); renderEff(); };
  $("#sum-dur-minus").addEventListener("click", () => bumpDur(-1));
  $("#sum-dur-plus").addEventListener("click", () => bumpDur(1));
  durInput.addEventListener("input", () => { clearTimeout(effTimer); effTimer = setTimeout(renderEff, 150); });

  $("#summary-done").addEventListener("click", async () => {
    const notes = $("#summary-notes").value.trim();
    const newDur = Math.max(1, parseInt(durInput.value, 10) || dur);
    if (newDur !== dur) { try { await apiPost(`${API}/sessions/${sid}/duration`, { minutes: newDur }); } catch (e) {} }
    if (notes) { try { await apiPost(`${API}/sessions/${sid}/notes`, { notes }); } catch (e) {} }
    clearSession();
    closeModal("summary-modal");
    switchTab("dashboard");
    loadWorkout();
  });
}

/* ══ 5. HISTORY ═══════════════════════════════════════════════════════════ */
let histMonth = null; // Date pointing at first of month
async function loadHistory() {
  if (!histMonth) { histMonth = new Date(); histMonth.setDate(1); histMonth.setHours(0,0,0,0); }
  renderHistoryCalendar();
  const sessions = await apiGet(`${API}/sessions?limit=40`);
  const wrap = $("#history-list"); wrap.innerHTML = "";
  if (!sessions.length) { wrap.innerHTML = `<div class="empty-note">No sessions logged yet.</div>`; return; }
  sessions.forEach(s => wrap.appendChild(historyRow(s)));
}
function historyRow(s) {
  const row = el("div", "hist-row");
  const d = new Date(s.date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" });
  row.innerHTML = `<div class="hist-summary">
      <span class="hist-date">${d}</span>
      <span class="hist-routine">${esc(s.routine_name || "Workout")}</span>
      <span class="hist-meta">${s.duration_minutes||0}m · ${s.total_sets||0} sets<br>${Math.round(s.total_volume_kg||0).toLocaleString()}kg · +${s.xp_earned||0}xp</span>
    </div><div class="hist-detail"></div>`;
  const summ = row.querySelector(".hist-summary");
  summ.addEventListener("click", async () => {
    row.classList.toggle("open");
    const det = row.querySelector(".hist-detail");
    if (row.classList.contains("open") && !det.dataset.loaded) {
      det.dataset.loaded = "1"; det.innerHTML = `<div class="muted-sub">Loading…</div>`;
      const full = await apiGet(`${API}/sessions/${s.id}`);
      det.innerHTML = renderSessionDetail(full);
    }
  });
  return row;
}
function renderSessionDetail(full) {
  const byEx = {};
  (full.sets || []).forEach(st => { (byEx[st.exercise_name] = byEx[st.exercise_name] || []).push(st); });
  let html = Object.keys(byEx).map(name => {
    const rows = byEx[name];
    const cardio = rows.some(s => s.exercise_type === "cardio" || s.muscle_group === "cardio");
    const sets = cardio
      ? rows.map(s => `${s.reps} min${s.notes ? " (" + esc(s.notes) + ")" : ""}`).join(", ")
      : rows.map(s => `${fmtKg(s.weight_kg)}kg×${s.reps}${s.is_pr ? " 🏆" : ""}`).join(", ");
    return `<div class="hist-ex-block"><div class="heb-name">${esc(name)}</div><div class="heb-set">${cardio ? sets : esc(sets)}</div></div>`;
  }).join("");
  if (!html) html = `<div class="muted-sub">No sets recorded.</div>`;
  if (full.notes) html += `<div class="hist-notes">📝 ${esc(full.notes)}</div>`;
  return html;
}
async function renderHistoryCalendar() {
  const label = $("#hist-month-label");
  label.textContent = histMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const wrap = $("#history-calendar"); wrap.innerHTML = "";
  ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].forEach(d => wrap.appendChild(el("div", "mc-dow", d)));
  const year = histMonth.getFullYear(), month = histMonth.getMonth();
  const first = new Date(year, month, 1); const startDow = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const todayStr = new Date().toISOString().slice(0, 10);
  // fetch sessions in range
  const cal = await apiGet(`${API}/sessions/calendar?months=1`).catch(() => ({}));
  const sessions = await apiGet(`${API}/sessions?limit=60`).catch(() => []);
  const byDate = {}; sessions.forEach(s => byDate[s.date] = s);
  for (let i = 0; i < startDow; i++) wrap.appendChild(el("div", "mc-day empty"));
  for (let d = 1; d <= daysInMonth; d++) {
    const key = `${year}-${String(month+1).padStart(2,"0")}-${String(d).padStart(2,"0")}`;
    const cell = el("div", "mc-day", String(d));
    const status = cal[key];
    if (byDate[key] || status === "workout") {
      cell.classList.add("worked");
      cell.title = byDate[key] ? (byDate[key].routine_name||"Workout") : "Workout";
      if (byDate[key]) cell.addEventListener("click", () => openSessionModal(byDate[key].id));
    } else if (status === "rest") {
      cell.classList.add("rest"); cell.title = "Rest day";
    }
    if (key === todayStr) cell.classList.add("today");
    wrap.appendChild(cell);
  }
}
async function openSessionModal(sessionId) {
  const full = await apiGet(`${API}/sessions/${sessionId}`);
  const body = $("#video-modal-body");
  const d = new Date(full.date + "T00:00:00").toLocaleDateString(undefined, { weekday:"long", month:"short", day:"numeric" });
  body.innerHTML = `<h2>${esc(full.routine_name || "Workout")}</h2>
    <div class="muted-sub" style="margin-bottom:12px">${d} · ${full.duration_minutes||0}min · ${Math.round(full.total_volume_kg||0).toLocaleString()}kg · +${full.xp_earned||0}xp</div>
    ${renderSessionDetail(full)}`;
  openModal("video-modal");
}
$("#hist-prev").addEventListener("click", () => { histMonth.setMonth(histMonth.getMonth() - 1); renderHistoryCalendar(); });
$("#hist-next").addEventListener("click", () => { histMonth.setMonth(histMonth.getMonth() + 1); renderHistoryCalendar(); });

/* ══ 6. EXERCISES ═════════════════════════════════════════════════════════ */
const FILTERS = ["All","Chest","Back","Shoulders","Biceps","Triceps","Quads","Hamstrings","Calves","Core","Cardio"];
let exFilter = "All", exQuery = "";
function loadExercises() {
  const fwrap = $("#ex-filters");
  if (!fwrap.children.length) {
    FILTERS.forEach(f => {
      const p = el("button", "filter-pill" + (f === exFilter ? " active" : ""), f); p.dataset.f = f;
      p.addEventListener("click", () => { exFilter = f; $$(".filter-pill").forEach(x => x.classList.toggle("active", x.dataset.f === f)); renderExGrid(); });
      fwrap.appendChild(p);
    });
    $("#ex-search").addEventListener("input", (e) => { exQuery = e.target.value.toLowerCase(); renderExGrid(); });
  }
  renderExGrid();
}
async function renderExGrid() {
  const grid = $("#ex-grid");
  await loadPRs();
  const prByEx = PR_BY_EX;
  const list = EXERCISES.filter(ex => {
    const okF = exFilter === "All" || ex.muscle_group.toLowerCase() === exFilter.toLowerCase();
    const okQ = !exQuery || ex.name.toLowerCase().includes(exQuery) || ex.muscle_group.toLowerCase().includes(exQuery);
    return okF && okQ;
  });
  grid.innerHTML = "";
  if (!list.length) { grid.innerHTML = `<div class="empty-note">No exercises match.</div>`; return; }
  list.forEach(ex => {
    const pr = prByEx[ex.id];
    const rank = pr ? computeRank(ex, pr.weight_kg) : null;
    const badge = rank ? `<span class="rank-badge rank-${rank.toLowerCase()}">${RANK_ICON[rank]} ${rank}</span>` : `<span class="rank-badge rank-unranked">Unranked</span>`;
    const strip = `<div class="rank-strip"><span>🥉${fmtKg(ex.rank_bronze)}</span><span>🥈${fmtKg(ex.rank_silver)}</span><span>🥇${fmtKg(ex.rank_gold)}</span><span>💎${fmtKg(ex.rank_platinum)}</span><span>💠${fmtKg(ex.rank_diamond)}+</span></div>`;
    const card = el("div", "ex-card sci-fi-panel");
    card.innerHTML = `<div class="exc-name">${esc(ex.name)}</div>
      <div class="exc-meta">${esc(ex.muscle_group)} · ${esc(ex.exercise_type||"")} · ${esc(ex.equipment||"")}</div>
      ${badge}
      ${pr ? `<div class="exc-pr">PR ${fmtKg(pr.weight_kg)}kg × ${pr.reps} · 1RM ${fmtKg(pr.one_rep_max)}kg</div>` : ""}
      ${strip}
      <div class="exc-row"><button class="btn btn-ghost how-btn">▶ How To</button></div>`;
    card.querySelector(".how-btn").addEventListener("click", () => openVideo(ex));
    corners(card); grid.appendChild(card);
  });
}

function ytEmbed(url) {
  if (!url) return null;
  let id = null;
  const m1 = url.match(/[?&]v=([^&]+)/); const m2 = url.match(/youtu\.be\/([^?]+)/); const m3 = url.match(/embed\/([^?]+)/);
  id = (m1 && m1[1]) || (m2 && m2[1]) || (m3 && m3[1]);
  return id ? `https://www.youtube.com/embed/${id}` : null;
}
async function openVideo(ex) {
  const body = $("#video-modal-body");
  const embed = ytEmbed(ex.youtube_url);
  const pr = PR_BY_EX[ex.id || ex.exerciseId] || null;
  let ormTable = "";
  if (pr && pr.one_rep_max) {
    const orm = pr.one_rep_max;
    ormTable = `<div class="modal-section"><h4>1RM Percentages</h4><table class="orm-table">` +
      [95,90,85,80,75,70].map(p => `<tr><td>${p}%</td><td>${fmtKg(round1(orm * p / 100))}kg</td></tr>`).join("") +
      `</table></div>`;
  }
  body.innerHTML = `<h2>${esc(ex.name)}</h2>
    <div class="muted-sub" style="margin-bottom:12px">${esc(ex.muscle_group)} · ${esc(ex.exercise_type||"")} · ${esc(ex.equipment||"")}</div>
    ${embed ? `<div class="video-frame"><iframe src="${embed}" allowfullscreen loading="lazy"></iframe></div>` : ""}
    ${ex.instructions ? `<div class="modal-section"><h4>How To</h4><div class="modal-steps">${esc(ex.instructions)}</div></div>` : ""}
    ${ex.tips ? `<div class="modal-section"><h4>Form Tips</h4><div class="form-tip">${esc(ex.tips)}</div></div>` : ""}
    ${ormTable}
    <div class="modal-section"><h4>Recent progress</h4><div class="chart-wrap" style="height:180px"><canvas id="mini-ex-chart"></canvas></div></div>`;
  openModal("video-modal");
  // mini progress chart
  const hist = await apiGet(`${API}/history/${ex.id || ex.exerciseId}?limit=10`).catch(() => []);
  const pts = hist.slice().reverse();
  killChart("mini");
  if (pts.length) {
    CHARTS.mini = new Chart($("#mini-ex-chart"), {
      type: "line",
      data: { labels: pts.map(p => new Date(p.date + "T00:00:00").toLocaleDateString(undefined, { month:"short", day:"numeric" })),
        datasets: [{ data: pts.map(p => p.weight_kg), borderColor: CYAN, backgroundColor: "rgba(0,217,255,.1)", fill: true, tension: .3, pointRadius: 3 }] },
      options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: gridScales() },
    });
  } else {
    $("#mini-ex-chart").parentElement.innerHTML = `<div class="empty-note">No history yet.</div>`;
  }
}

/* ══ 7. PROGRESS ══════════════════════════════════════════════════════════ */
async function loadProgress() {
  await loadPRs();
  renderXpBar();
  renderBodyweightChart();
  renderRankingsOverview();
  setupExerciseProgress();
}
async function renderXpBar() {
  const wrap = $("#xp-progress");
  const xp = await apiGet(`${API}/xp`);
  const tiers = [["Bronze",0],["Silver",500],["Gold",1500],["Platinum",3500],["Diamond",7000]];
  const total = xp.total_xp || 0; const rank = xp.overall_rank || "Bronze";
  let idx = 0; for (let i = 0; i < tiers.length; i++) if (total >= tiers[i][1]) idx = i;
  const next = tiers[idx + 1];
  if (!next) {
    wrap.innerHTML = `<div class="xp-label"><span>${RANK_ICON[rank]} ${rank} — max rank!</span><b>${total.toLocaleString()} XP</b></div>
      <div class="xp-bar-track"><div class="xp-bar-fill" style="width:100%"></div></div>`;
    return;
  }
  const base = tiers[idx][1]; const span = next[1] - base; const pct = Math.min(100, Math.round((total - base) / span * 100));
  wrap.innerHTML = `<div class="xp-label"><span>${RANK_ICON[rank]} ${rank}</span><b>${total.toLocaleString()} / ${next[1].toLocaleString()} XP → ${next[0]} ${RANK_ICON[next[0]]}</b></div>
    <div class="xp-bar-track"><div class="xp-bar-fill" style="width:${pct}%"></div></div>`;
}
function movingAverage(vals, win) {
  return vals.map((_, i) => { const s = Math.max(0, i - win + 1); const slice = vals.slice(s, i + 1); return round1(slice.reduce((a, b) => a + b, 0) / slice.length); });
}
async function renderBodyweightChart() {
  const stats = await apiGet(`${API}/body-stats?limit=90`);
  const pts = stats.slice().reverse(); // oldest first
  const change = $("#bw-change");
  killChart("bw");
  if (!pts.length) { change.textContent = ""; $("#bw-wrap").innerHTML = `<div class="empty-note">Log your bodyweight to see the trend.</div>`; return; }
  $("#bw-wrap").innerHTML = `<canvas id="bw-chart"></canvas>`;
  const weights = pts.map(p => p.weight_kg);
  const diff = round1(weights[weights.length - 1] - weights[0]);
  const firstDate = new Date(pts[0].date + "T00:00:00").toLocaleDateString(undefined, { month:"short", day:"numeric" });
  change.textContent = `${diff >= 0 ? "+" : ""}${diff}kg since ${firstDate}`;
  change.style.color = diff <= 0 ? "var(--gym-green)" : "var(--gym-amber)";
  const ma = movingAverage(weights, 7);
  CHARTS.bw = new Chart($("#bw-chart"), {
    type: "line",
    data: { labels: pts.map(p => new Date(p.date + "T00:00:00").toLocaleDateString(undefined, { month:"short", day:"numeric" })),
      datasets: [
        { label: "Bodyweight", data: weights, borderColor: CYAN, backgroundColor: "rgba(0,217,255,.1)", fill: true, tension: .3, pointRadius: 2 },
        { label: "7-day avg", data: ma, borderColor: GOLD, borderDash: [5,4], fill: false, tension: .3, pointRadius: 0 },
      ] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: "#8aa" } } }, scales: gridScales() },
  });
}
async function renderRankingsOverview() {
  const wrap = $("#rankings-overview");
  const [ranks, prs] = await Promise.all([apiGet(`${API}/ranks`), apiGet(`${API}/prs`)]);
  const best = bestLiftByMuscle(prs);
  const rankMap = {}; ranks.forEach(r => rankMap[r.muscle_group] = r);
  const groups = Array.from(new Set([...ranks.map(r => r.muscle_group), ...Object.keys(best)]));
  if (!groups.length) { wrap.innerHTML = `<div class="empty-note">No rankings yet.</div>`; return; }
  groups.sort((a, b) => (RANK_ORDER.indexOf((rankMap[b]||{}).current_rank) - RANK_ORDER.indexOf((rankMap[a]||{}).current_rank)));
  wrap.innerHTML = "";
  groups.forEach(g => {
    const rk = rankMap[g]; const rank = rk ? rk.current_rank : "Bronze"; const ranked = !!rk;
    const color = ranked ? RANK_COLORS[rank] : "#3a4a5a";
    const pct = ((RANK_ORDER.indexOf(rank) + 1) / RANK_ORDER.length) * 100;
    const lift = best[g] ? `${esc(best[g].exercise_name)} ${fmtKg(best[g].weight_kg)}kg` : "no lift yet";
    const row = el("div", "ro-row");
    row.innerHTML = `<div class="ro-top"><span class="ro-name">${esc(g)}</span>
        <span class="rank-badge rank-${(ranked?rank:"unranked").toLowerCase()}">${ranked ? RANK_ICON[rank]+" "+rank : "Unranked"}</span></div>
      <div class="rl-bar"><span style="width:${ranked?pct:6}%;background:${color}"></span></div>
      <div class="ro-lift">${g} — ${ranked ? rank : "Unranked"} via ${lift}</div>`;
    wrap.appendChild(row);
  });
}
async function setupExerciseProgress() {
  const sel = $("#prog-ex-select");
  if (!sel.children.length) {
    // group by muscle
    const byMuscle = {};
    EXERCISES.forEach(ex => { (byMuscle[ex.muscle_group] = byMuscle[ex.muscle_group] || []).push(ex); });
    Object.keys(byMuscle).sort().forEach(m => {
      const og = el("optgroup"); og.label = m;
      byMuscle[m].forEach(ex => { const o = el("option", null, esc(ex.name)); o.value = ex.id; og.appendChild(o); });
      sel.appendChild(og);
    });
    sel.addEventListener("change", () => renderExerciseProgress(+sel.value));
  }
  if (sel.value) renderExerciseProgress(+sel.value);
}
async function renderExerciseProgress(exId) {
  const hist = await apiGet(`${API}/history/${exId}?limit=40`);
  const pr = PR_BY_EX[exId] || null;
  const pts = hist.slice().reverse();
  const statsWrap = $("#prog-ex-stats");
  const totalVol = pts.reduce((a, p) => a + (p.weight_kg || 0) * (p.reps || 0), 0);
  statsWrap.innerHTML = `
    <div class="ps"><b>${pr ? fmtKg(pr.weight_kg)+"kg" : "—"}</b><small>PR</small></div>
    <div class="ps"><b>${pr ? fmtKg(pr.one_rep_max)+"kg" : "—"}</b><small>est 1RM</small></div>
    <div class="ps"><b>${pts.length}</b><small>sessions</small></div>
    <div class="ps"><b>${Math.round(totalVol).toLocaleString()}</b><small>kg total</small></div>`;
  killChart("progEx");
  if (!pts.length) { $("#prog-wrap").innerHTML = `<div class="empty-note">No history for this exercise yet.</div>`; return; }
  $("#prog-wrap").innerHTML = `<canvas id="prog-ex-chart"></canvas>`;
  CHARTS.progEx = new Chart($("#prog-ex-chart"), {
    type: "line",
    data: { labels: pts.map(p => new Date(p.date + "T00:00:00").toLocaleDateString(undefined, { month:"short", day:"numeric" })),
      datasets: [
        { label: "Best set (kg)", data: pts.map(p => p.weight_kg), borderColor: CYAN, backgroundColor: "rgba(0,217,255,.12)", fill: true, tension: .3, pointRadius: 3 },
        { label: "est 1RM", data: pts.map(p => p.one_rep_max), borderColor: GOLD, borderDash: [5,4], fill: false, tension: .3, pointRadius: 0 },
      ] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: "#8aa" } } }, scales: gridScales() },
  });
}

/* bodyweight log form */
$("#bw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const v = parseFloat($("#bw-input").value);
  if (isNaN(v) || v < 20 || v > 300) { toast("Enter a valid weight"); return; }
  try { await apiPost(`${API}/body-stats`, { weight_kg: v }); $("#bw-input").value = ""; toast("Logged"); renderBodyweightChart(); }
  catch (e2) { toast("Could not log weight"); }
});

/* ── Chart grid helper ── */
function gridScales(noXgrid) {
  return {
    x: { grid: { color: "rgba(140,170,200,.06)" }, ticks: { color: "#6a8", font: { size: 9 } } },
    y: { grid: { color: noXgrid ? "rgba(140,170,200,.06)" : "rgba(140,170,200,.06)" }, ticks: { color: "#6a8", font: { size: 10 } }, beginAtZero: false },
  };
}

/* ── Modals ── */
function openModal(id) { const m = $("#" + id); m.hidden = false; }
function closeModal(id) { const m = $("#" + id); m.hidden = true; if (id === "video-modal") $("#video-modal-body").innerHTML = ""; killChart("mini"); }
$$("[data-close-modal]").forEach(b => b.addEventListener("click", () => closeModal(b.dataset.closeModal)));
$$(".modal-overlay").forEach(ov => ov.addEventListener("click", (e) => { if (e.target === ov) closeModal(ov.id); }));

/* ══ 8. ADD-EXERCISE PICKER (session-only) ═════════════════════════════════ */
let addexFilter = "All", addexQuery = "";
function openAddExercise() {
  if (!S) { toast("Start a session first"); return; }
  const fwrap = $("#addex-filters");
  if (!fwrap.children.length) {
    FILTERS.forEach(f => {
      const p = el("button", "filter-pill" + (f === addexFilter ? " active" : ""), f); p.dataset.f = f;
      p.addEventListener("click", () => { addexFilter = f; $$("#addex-filters .filter-pill").forEach(x => x.classList.toggle("active", x.dataset.f === f)); renderAddexList(); });
      fwrap.appendChild(p);
    });
    $("#addex-search").addEventListener("input", (e) => { addexQuery = e.target.value.toLowerCase(); renderAddexList(); });
  }
  renderAddexList();
  openModal("addex-modal");
}
function renderAddexList() {
  const wrap = $("#addex-list"); wrap.innerHTML = "";
  const inSession = new Set((S ? S.exercises : []).map(e => e.exerciseId));
  const list = EXERCISES.filter(ex => {
    const okF = addexFilter === "All" || ex.muscle_group.toLowerCase() === addexFilter.toLowerCase();
    const okQ = !addexQuery || ex.name.toLowerCase().includes(addexQuery) || ex.muscle_group.toLowerCase().includes(addexQuery);
    return okF && okQ;
  });
  if (!list.length) { wrap.innerHTML = `<div class="empty-note">No exercises match.</div>`; return; }
  list.forEach(ex => {
    const already = inSession.has(ex.id);
    const b = el("button", "qs-btn" + (already ? " disabled" : ""));
    b.innerHTML = `<span>${esc(ex.name)}<br><span class="qs-meta">${esc(ex.muscle_group)} · ${esc(ex.equipment||ex.exercise_type||"")}</span></span>
      <span class="qs-meta">${already ? "✓ added" : "Add +"}</span>`;
    if (!already) b.addEventListener("click", () => { addExerciseToSession(ex); closeModal("addex-modal"); });
    wrap.appendChild(b);
  });
}
(function wireAddExercise() {
  const btn = $("#add-exercise-btn");
  if (btn) btn.addEventListener("click", openAddExercise);
})();

/* ══ 9. AI TRAINER + SETTINGS ══════════════════════════════════════════════ */
function applyAiVisibility() {
  const panel = $("#trainer-panel");
  if (!panel) return;
  panel.hidden = !aiEnabled();
}
function buildTrainerContext(message) {
  const ctx = { profile: "17yo, ~6 months training, 79kg, goal = body recomposition" };
  if (S) ctx.routine = S.routineName;
  const ml = (message || "").toLowerCase();
  // Prefer an exercise explicitly named in the question, else the current one.
  let ex = EXERCISES.find(e => ml.includes(e.name.toLowerCase()));
  if (!ex && CURRENT_EX_ID) ex = EX_BY_ID[CURRENT_EX_ID];
  if (!ex && S && S.exercises.length) ex = EX_BY_ID[S.exercises[0].exerciseId];
  if (ex) {
    ctx.exercise = ex.name + (ex.muscle_group ? ` (${ex.muscle_group})` : "");
    const pr = PR_BY_EX[ex.id];
    if (pr) ctx.pr = `${fmtKg(pr.weight_kg)}kg × ${pr.reps} (est 1RM ${fmtKg(pr.one_rep_max)}kg)`;
    if (ex.youtube_url) ctx.youtube_url = ex.youtube_url;
  }
  return ctx;
}
function trainerAppend(role, text) {
  const log = $("#trainer-log");
  const msg = el("div", "trainer-msg " + role, role === "you" ? esc(text) : esc(text));
  log.appendChild(msg);
  log.scrollTop = log.scrollHeight;
  return msg;
}
async function submitTrainer(e) {
  e.preventDefault();
  if (!aiEnabled()) return;   // never call the API when disabled
  const input = $("#trainer-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  trainerAppend("you", message);
  const thinking = trainerAppend("coach", "…");
  try {
    const res = await apiPost(`${API}/trainer`, { message, context: buildTrainerContext(message) });
    thinking.textContent = res.reply || "No reply.";
  } catch (err) {
    thinking.textContent = "Trainer unavailable right now.";
  }
  $("#trainer-log").scrollTop = $("#trainer-log").scrollHeight;
}
function initSettings() {
  const chk = $("#setting-ai-trainer");
  if (chk) {
    chk.checked = aiEnabled();
    chk.addEventListener("change", () => {
      localStorage.setItem(LS_AI, chk.checked ? "1" : "0");
      applyAiVisibility();
      toast(chk.checked ? "AI Trainer on" : "AI Trainer off");
    });
  }
  const openBtn = $("#gym-settings-btn");
  if (openBtn) openBtn.addEventListener("click", () => { if (chk) chk.checked = aiEnabled(); openModal("settings-modal"); });
}
function initTrainer() {
  applyAiVisibility();
  const form = $("#trainer-form");
  if (form) form.addEventListener("submit", submitTrainer);
  const collapse = $("#trainer-collapse"), head = $("#trainer-head");
  const toggleCollapse = () => {
    const panel = $("#trainer-panel");
    panel.classList.toggle("collapsed");
    if (collapse) collapse.textContent = panel.classList.contains("collapsed") ? "▴" : "▾";
  };
  if (collapse) collapse.addEventListener("click", (e) => { e.stopPropagation(); toggleCollapse(); });
  if (head) head.addEventListener("click", toggleCollapse);
}

/* ── Loaders registry ── */
const LOADERS = {
  dashboard: loadDashboard, workout: loadWorkout, history: loadHistory,
  exercises: loadExercises, progress: loadProgress,
};

/* ── Boot ── */
async function boot() {
  try {
    [EXERCISES, ROUTINES] = await Promise.all([apiGet(`${API}/exercises`), apiGet(`${API}/routines`)]);
    EX_BY_ID = {}; EXERCISES.forEach(ex => EX_BY_ID[ex.id] = ex);
    await loadPRs();
  } catch (e) { console.error("boot failed", e); toast("Could not load gym data — are you logged in?"); }
  initSettings();
  initTrainer();
  // restore in-memory session from localStorage if present
  const saved = localStorage.getItem(LS_KEY);
  if (saved) { try { S = JSON.parse(saved); } catch (e) { S = null; } }
  const initial = (location.hash || "").replace("#", "");
  const tab = ["dashboard","workout","history","exercises","progress"].includes(initial) ? initial : "dashboard";
  switchTab(S ? "workout" : tab);
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();

})();
