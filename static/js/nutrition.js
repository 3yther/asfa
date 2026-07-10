/* ══════════════════════════════════════════════════════════════════════════
   nutrition.js — Fuel log frontend for the standalone /nutrition page.
   Vanilla JS, self-contained (own api/toast/esc helpers) so it runs on the
   /nutrition page without main.js — same pattern as gym.js on /gym.
   Reuses the existing /api/nutrition/* endpoints unchanged:
     goals · date/<date> · search · log · undo · previous-foods · frequent-at-hour
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
"use strict";

/* ── Helpers (mirror main.js / gym.js) ────────────────────────────────────── */
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
function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function toast(msg, ms = 2200) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

// ── Fuel log (search-first nutrition surface) ────────────────────────────────────
// The /nutrition page's core. Reads the meals store via /api/nutrition/*.
// Search layers three sources into one dropdown: instant
// LOCAL matches (previously logged foods + time-of-day patterns) shown first,
// then live USDA/FDC whole-food results (per-100g) via /api/nutrition/search.
// Barcode lookup (Open Food Facts) covers packaged goods on its own pane.
const NH = {
  date: null,      // YYYY-MM-DD currently viewed
  goals: null,     // {protein_goal, carbs_goal, fat_goal, calorie_goal}
  prev: [],        // previous foods [{food_name, count, protein, carbs, fat, calories}]
  freq: [],        // frequent-at-hour [{food_name, count}]
  picked: null,    // {food_name, protein, carbs, fat, mode:"per100"|"serving"}
  favorites: [],   // [{food_name, count, protein, carbs, fat, calories}] averaged
  templates: [],   // [{id, name, items, item_count, totals}]
  meals: [],       // this day's meal rows (for the save-as-template modal)
  charts: {},      // Chart.js instances, created once then updated (no leak)
};

function nhToday() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function nhShiftDate(dateStr, delta) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d + delta), p = (n) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
}
function nhDateLabel(dateStr) {
  const today = nhToday();
  if (dateStr === today) return "Today";
  if (dateStr === nhShiftDate(today, -1)) return "Yesterday";
  if (dateStr === nhShiftDate(today, 1)) return "Tomorrow";
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB",
    { weekday: "short", day: "numeric", month: "short" });
}
// Meal → time-of-day bucket. No time (or out-of-window) falls to Snack.
function nhBucket(time) {
  if (!time || !/^\d{2}:\d{2}/.test(time)) return "Snack";
  const h = parseInt(time.slice(0, 2), 10);
  if (h >= 5 && h <= 10) return "Breakfast";
  if (h >= 11 && h <= 15) return "Lunch";
  if (h >= 16 && h <= 21) return "Dinner";
  return "Snack";
}
const NH_BUCKETS = ["Breakfast", "Lunch", "Dinner", "Snack"];
const nhNum = (v) => (v == null ? 0 : Math.round(Number(v) * 10) / 10);

// Drive an SVG progress ring: pct 0-100 maps to stroke-dashoffset. Circumference
// is derived from the circle's own r so markup and script never drift.
function setRing(el, pct) {
  if (!el) return;
  const r = Number(el.getAttribute("r")) || 0;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(100, Number(pct) || 0));
  el.style.strokeDasharray = String(c);
  el.style.strokeDashoffset = String(c * (1 - p / 100));
}

function nhRenderReadouts(totals, goals) {
  const g = goals || {};
  const t = totals || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  const left = (goal, used) => Math.max(0, Math.round((Number(goal) || 0) - (Number(used) || 0)));
  const pct = (used, goal) => {
    const gg = Number(goal) || 0; if (gg <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((Number(used) || 0) / gg * 100)));
  };
  // Centre readouts: calories keep the "kcal" suffix; macros are bare grams-left.
  set("nh-cals-left", left(g.calorie_goal, t.total_calories));
  set("nh-protein-left", left(g.protein_goal, t.total_protein));
  set("nh-carbs-left", left(g.carbs_goal, t.total_carbs));
  set("nh-fat-left", left(g.fat_goal, t.total_fat));
  // Rings fill toward consumed/goal (capped 100%).
  const ring = (id, used, goal) => setRing(document.getElementById(id), pct(used, goal));
  ring("nh-cals-ring", t.total_calories, g.calorie_goal);
  ring("nh-protein-ring", t.total_protein, g.protein_goal);
  ring("nh-carbs-ring", t.total_carbs, g.carbs_goal);
  ring("nh-fat-ring", t.total_fat, g.fat_goal);
}

function nhRenderMeals(meals) {
  const wrap = document.getElementById("nh-meals");
  const empty = document.getElementById("nh-empty");
  if (!wrap || !empty) return;
  const list = Array.isArray(meals) ? meals : [];
  if (!list.length) {
    wrap.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  // Undo removes the most recent meal (highest id); only that row gets the icon.
  let lastId = -Infinity;
  list.forEach((m) => { if (Number(m.id) > lastId) lastId = Number(m.id); });
  const groups = {};
  NH_BUCKETS.forEach((b) => { groups[b] = []; });
  list.forEach((m) => { groups[nhBucket(m.time)].push(m); });
  let html = "";
  NH_BUCKETS.forEach((b) => {
    const rows = groups[b];
    if (!rows.length) return;                       // collapse empty sections
    // The save-as-template icon pre-checks this section's meals in the modal.
    html += `<div class="nh-meal-group"><div class="hud-label nh-group-head">` +
      `<span>${b}</span>` +
      `<button type="button" class="nh-save-tpl" data-nh-save-tpl="${b}" ` +
      `title="Save ${b} as template" aria-label="Save ${b} as template">▾ SAVE</button></div>`;
    rows.forEach((m) => {
      const macros = `${nhNum(m.protein)}P · ${nhNum(m.carbs)}C · ${nhNum(m.fat)}F`;
      const undo = Number(m.id) === lastId
        ? `<button type="button" class="nh-undo" data-nh-undo aria-label="Undo last meal" title="Undo last">↶</button>`
        : "";
      // Every row gets a delete icon so any meal can be removed, not just the last.
      const del = `<button type="button" class="nh-del" data-nh-del="${m.id}" ` +
        `data-nh-food="${esc(m.food_name)}" aria-label="Delete meal" title="Delete">🗑</button>`;
      // Show the serving note (e.g. "0.5 cup") beside the name when present.
      const serving = m.notes ? `<span class="nm-serving">${esc(m.notes)}</span>` : "";
      html += `<div class="nh-meal">` +
        `<span class="nm-name">${esc(m.food_name)}${serving}</span>` +
        `<span class="nm-macros">${esc(macros)}</span>${undo}${del}</div>`;
    });
    html += `</div>`;
  });
  wrap.innerHTML = html;
}

function nhRenderDay(day) {
  if (!day) return;
  NH.goals = day.goals || NH.goals;
  NH.meals = Array.isArray(day.meals) ? day.meals : [];
  nhRenderReadouts(day.totals, NH.goals);
  nhRenderMeals(day.meals);
  const label = document.getElementById("nh-date");
  if (label) { label.textContent = nhDateLabel(NH.date); label.dataset.date = NH.date; }
}

// Sunday (Mon–Sun week) of the viewed date — anchors the week-strip window so
// trends(7, end=Sunday) returns Monday…Sunday of that week.
function nhWeekEndSunday(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const day = new Date(y, m - 1, d).getDay();   // 0=Sun … 6=Sat
  const iso = day === 0 ? 7 : day;              // 1=Mon … 7=Sun
  return nhShiftDate(dateStr, 7 - iso);
}

// Full page load / refresh. Every widget fetch is independent (allSettled) and
// renders in its own guarded path, so one failing endpoint shows a placeholder
// rather than blanking the page. Called on init, on date change, and after any
// log/undo so totals · rings · week strip · score all move together.
async function fetchNutritionHub() {
  const root = document.getElementById("nutrition-screen");
  if (!root) return;
  if (!NH.date) NH.date = nhToday();
  const hour = new Date().getHours();
  const weekEnd = nhWeekEndSunday(NH.date);
  const [goals, day, prev, freq, trends7, trends30, score, favorites, templates, insights] =
    await Promise.allSettled([
      apiGet("/api/nutrition/goals"),
      apiGet(`/api/nutrition/date/${NH.date}`),
      apiGet("/api/nutrition/previous-foods?limit=50"),
      apiGet(`/api/nutrition/frequent-at-hour?hour=${hour}&limit=5`),
      apiGet(`/api/nutrition/trends?days=7&end=${weekEnd}`),
      apiGet("/api/nutrition/trends?days=30"),
      apiGet(`/api/nutrition/score?date=${NH.date}`),
      apiGet("/api/nutrition/favorites?limit=6"),
      apiGet("/api/nutrition/templates"),
      apiGet("/api/nutrition/insights"),
    ]);

  if (goals.status === "fulfilled") NH.goals = goals.value;
  if (prev.status === "fulfilled" && Array.isArray(prev.value)) NH.prev = prev.value;
  if (freq.status === "fulfilled" && Array.isArray(freq.value)) NH.freq = freq.value;

  if (day.status === "fulfilled") {
    nhRenderDay(day.value);
  } else {
    nhRenderReadouts({}, NH.goals);   // never blank: goals with zero consumed
    nhRenderMeals([]);
    const label = document.getElementById("nh-date");
    if (label) label.textContent = nhDateLabel(NH.date);
  }

  if (trends7.status === "fulfilled") nhRenderWeek(trends7.value);
  else nhWidgetError("nh-week", "week unavailable");
  if (trends7.status === "fulfilled") nhRenderTrendsChart(trends7.value);
  if (trends30.status === "fulfilled") nhRenderBalanceChart(trends30.value);
  if (score.status === "fulfilled") nhRenderScore(score.value);
  else nhWidgetError("nh-score-sub", "score unavailable");
  if (favorites.status === "fulfilled") nhRenderFavorites(favorites.value);
  if (templates.status === "fulfilled") nhRenderTemplates(templates.value);
  if (insights.status === "fulfilled") nhRenderInsights(insights.value);
  else nhRenderInsights(null);
}

// Single unified refresh so every day-derived widget moves together after a log,
// undo, template log, or goal change. (Alias kept expressive at call sites.)
const refreshDay = fetchNutritionHub;

function nhWidgetError(id, msg) {
  const el = document.getElementById(id);
  if (el && el.tagName === "DIV" && !el.children.length) el.textContent = msg;
}

// ── Week strip ───────────────────────────────────────────────────────────────
function nhRenderWeek(trends) {
  const wrap = document.getElementById("nh-week");
  if (!wrap || !trends) return;
  const dates = trends.dates || [];
  const g = trends.goals || {};
  const cap = (v, goal) => {
    const gg = Number(goal) || 0; if (gg <= 0) return 0;
    return Math.max(0, Math.min(100, (Number(v) || 0) / gg * 100));
  };
  const bars = (i) => [
    ["nh-wbar-1", cap(trends.kcal[i], g.calorie_goal)],
    ["nh-wbar-2", cap(trends.protein[i], g.protein_goal)],
    ["nh-wbar-3", cap(trends.carbs[i], g.carbs_goal)],
    ["nh-wbar-4", cap(trends.fat[i], g.fat_goal)],
  ].map(([cls, pct]) =>
    `<span class="nh-wbar ${cls}"><i style="height:${pct.toFixed(1)}%"></i></span>`).join("");
  const dow = (ds) => {
    const [y, m, d] = ds.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("en-GB", { weekday: "short" });
  };
  wrap.innerHTML = dates.map((ds, i) =>
    `<div class="nh-week-col${ds === NH.date ? " current" : ""}" data-nh-day="${ds}" ` +
    `role="button" tabindex="0" aria-label="${ds}">` +
    `<span class="nh-week-day">${dow(ds)[0]}</span>` +
    `<span class="nh-week-bars">${bars(i)}</span></div>`).join("");
}

// ── Day score + streak ───────────────────────────────────────────────────────
function nhRenderScore(s) {
  if (!s) return;
  const grade = document.getElementById("nh-grade");
  const sub = document.getElementById("nh-score-sub");
  const streak = document.getElementById("nh-streak");
  if (grade) { grade.textContent = s.grade || "–"; grade.dataset.grade = s.logged ? (s.grade || "") : ""; }
  if (sub) {
    if (!s.logged) sub.textContent = "No meals logged yet.";
    else if (!s.misses || !s.misses.length) sub.textContent = `${s.hits}/4 goals met — nailed it.`;
    else sub.textContent = `${s.hits}/4 goals met — ${s.misses.join(", ")} off.`;
  }
  if (streak) streak.textContent = s.streak > 0 ? `${s.streak}-day A/B streak` : "";
}

// ── Charts (created once, then updated — never recreated per navigation) ──────
function nhChartReady() { return typeof Chart !== "undefined"; }
const NH_GRID = "rgba(140,170,200,.10)";
const NH_TICK = "#8296A5";
function nhCssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function nhRenderTrendsChart(trends) {
  const cv = document.getElementById("nh-trends-chart");
  if (!cv || !nhChartReady() || !trends) return;
  const g = trends.goals || {};
  const line = (data, color) => ({
    data, borderColor: color, backgroundColor: color, tension: .3,
    pointRadius: 2, borderWidth: 2, fill: false,
  });
  const goalLine = (val, color) => ({
    data: trends.dates.map(() => val), borderColor: color, borderDash: [4, 4],
    borderWidth: 1, pointRadius: 0, fill: false,
  });
  const labels = trends.dates.map((d) => d.slice(5));  // MM-DD
  const s1 = nhCssVar("--series-1"), s2 = nhCssVar("--series-2"), s3 = nhCssVar("--series-3");
  const datasets = [
    { label: "Protein", ...line(trends.protein, s1) },
    { label: "Carbs", ...line(trends.carbs, s2) },
    { label: "Fat", ...line(trends.fat, s3) },
    { label: "P goal", ...goalLine(g.protein_goal, s1) },
    { label: "C goal", ...goalLine(g.carbs_goal, s2) },
    { label: "F goal", ...goalLine(g.fat_goal, s3) },
  ];
  if (NH.charts.trends) {
    NH.charts.trends.data.labels = labels;
    NH.charts.trends.data.datasets.forEach((ds, i) => { ds.data = datasets[i].data; });
    NH.charts.trends.update();
    return;
  }
  NH.charts.trends = new Chart(cv, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: {
        legend: { labels: { color: NH_TICK, font: { size: 9 }, filter: (l) => !l.text.includes("goal") } },
        tooltip: { mode: "index", intersect: false },
      },
      scales: {
        x: { grid: { color: NH_GRID }, ticks: { color: NH_TICK, font: { size: 9 } } },
        y: { grid: { color: NH_GRID }, ticks: { color: NH_TICK, font: { size: 9 } }, beginAtZero: true },
      },
    },
  });
}

function nhRenderBalanceChart(trends) {
  const cv = document.getElementById("nh-balance-chart");
  if (!cv || !nhChartReady() || !trends) return;
  const goal = Number((trends.goals || {}).calorie_goal) || 0;
  const labels = trends.dates.map((d) => d.slice(5));
  const bars = trends.kcal;
  const deep = nhCssVar("--cyan-deep"), warn = nhCssVar("--warn");
  if (NH.charts.balance) {
    NH.charts.balance.data.labels = labels;
    NH.charts.balance.data.datasets[0].data = bars;
    NH.charts.balance.data.datasets[1].data = labels.map(() => goal);
    NH.charts.balance.update();
    return;
  }
  NH.charts.balance = new Chart(cv, {
    type: "bar",
    data: {
      labels,
      datasets: [
        { type: "bar", label: "Intake", data: bars, backgroundColor: deep, borderRadius: 2, barPercentage: .9 },
        { type: "line", label: "Goal", data: labels.map(() => goal), borderColor: warn,
          borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: NH_TICK, font: { size: 8 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
        y: { grid: { color: NH_GRID }, ticks: { color: NH_TICK, font: { size: 9 } }, beginAtZero: true },
      },
    },
  });
}

// ── One-tap pill rows ─────────────────────────────────────────────────────────
function nhRenderFavorites(favs) {
  const wrap = document.getElementById("nh-favorites");
  if (!wrap) return;
  const list = Array.isArray(favs) ? favs.slice(0, 6) : [];
  wrap.innerHTML = list.map((f) =>
    `<button type="button" class="nh-pill" data-nh-fav="${esc(f.food_name)}">` +
    `${esc(f.food_name)}<span class="nh-pill-meta">×${f.count}</span></button>`).join("");
}

function nhRenderTemplates(tpls) {
  const wrap = document.getElementById("nh-templates");
  if (!wrap) return;
  NH.templates = Array.isArray(tpls) ? tpls : [];
  const pills = NH.templates.map((t) =>
    `<button type="button" class="nh-pill nh-pill-tpl" data-nh-tpl="${t.id}">` +
    `${esc(t.name)}<span class="nh-pill-meta">· ${Math.round((t.totals || {}).kcal || 0)} kcal</span>` +
    `</button>`).join("");
  wrap.innerHTML = pills +
    `<button type="button" class="nh-pill nh-pill-new" id="nh-new-template">+ new template</button>`;
}

function nhRenderInsights(lines) {
  const wrap = document.getElementById("nh-insights");
  if (!wrap) return;
  const list = Array.isArray(lines) ? lines : [];
  if (!list.length) {
    wrap.innerHTML = `<li class="nh-insights-empty">Insights unavailable right now.</li>`;
    return;
  }
  wrap.innerHTML = list.map((s) => `<li>${esc(s)}</li>`).join("");
}

// Navigate the whole page to a specific date (week-strip column click).
function goToDate(dateStr) {
  if (!dateStr || dateStr === NH.date) return;
  NH.date = dateStr;
  fetchNutritionHub();
}

// ── Favorites: one-tap re-log using averaged macros ──────────────────────────
async function nhLogFavorite(name) {
  if (!name) return;
  try {
    await apiPost("/api/nutrition/log-favorite", { food_name: name, date: NH.date });
    toast("LOGGED " + name.toUpperCase());
    await refreshDay();
  } catch { toast("LOG FAILED"); }
}

// ── Templates: log an existing one (with a confirm), or build a new one ───────
let nhPendingTpl = null;

function nhConfirmLogTemplate(tplId) {
  const tpl = NH.templates.find((t) => String(t.id) === String(tplId));
  if (!tpl) return;
  nhPendingTpl = tpl.id;
  const sub = document.getElementById("nh-tpl-confirm-sub");
  if (sub) sub.textContent =
    `Log ${tpl.item_count} item${tpl.item_count === 1 ? "" : "s"} from “${tpl.name}”?`;
  nhModal("nh-tpl-confirm-modal", true);
}

async function nhDoLogTemplate() {
  if (nhPendingTpl == null) return;
  const btn = document.getElementById("nh-tpl-confirm-ok");
  if (btn) btn.disabled = true;
  try {
    const d = await apiPost("/api/nutrition/log-template",
      { template_id: nhPendingTpl, date: NH.date });
    nhModal("nh-tpl-confirm-modal", false);
    toast(`LOGGED ${d.meals_logged || 0} ITEM${d.meals_logged === 1 ? "" : "S"}`);
    nhPendingTpl = null;
    await refreshDay();
  } catch {
    toast("LOG FAILED");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Build the new-template modal from the day's meals. `preselect` (a bucket name)
// pre-checks that section's meals when opened from a meal-section save icon.
function nhOpenTemplateModal(preselect) {
  const list = document.getElementById("nh-template-list");
  const name = document.getElementById("nh-template-name");
  const err = document.getElementById("nh-template-err");
  if (err) err.textContent = "";
  const meals = NH.meals || [];
  if (!meals.length) { toast("NO MEALS TO SAVE ON THIS DAY"); return; }
  if (name) name.value = preselect || "";
  if (list) {
    list.innerHTML = meals.map((m) => {
      const checked = !preselect || nhBucket(m.time) === preselect ? "checked" : "";
      return `<li class="nh-copy-item"><label>` +
        `<input type="checkbox" data-nh-tpl-meal="${m.id}" ${checked}> ` +
        `<span class="nm-name">${esc(m.food_name)}</span>` +
        `<span class="nm-macros">${nhNum(m.protein)}P · ${nhNum(m.carbs)}C · ${nhNum(m.fat)}F</span>` +
        `</label></li>`;
    }).join("");
  }
  nhModal("nh-template-modal", true);
}

async function nhSaveTemplate() {
  const err = document.getElementById("nh-template-err");
  const setErr = (m) => { if (err) err.textContent = m || ""; };
  setErr("");
  const name = (document.getElementById("nh-template-name")?.value || "").trim();
  if (!name) { setErr("NAME THE TEMPLATE"); return; }
  const ids = [...document.querySelectorAll("[data-nh-tpl-meal]")]
    .filter((cb) => cb.checked).map((cb) => parseInt(cb.dataset.nhTplMeal, 10))
    .filter((n) => !isNaN(n));
  if (!ids.length) { setErr("PICK AT LEAST ONE MEAL"); return; }
  const btn = document.getElementById("nh-template-save");
  if (btn) btn.disabled = true;
  try {
    await apiPost("/api/nutrition/template", { name, meal_ids: ids });
    nhModal("nh-template-modal", false);
    toast("TEMPLATE SAVED");
    await refreshDay();
  } catch {
    setErr("SAVE FAILED — TRY AGAIN");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Hero carousel (Today rings / Balance chart) ──────────────────────────────
function nhSwitchHero(view) {
  document.querySelectorAll(".nh-dot").forEach((d) =>
    d.classList.toggle("active", d.dataset.heroView === view));
  document.querySelectorAll(".nh-hero-view").forEach((v) =>
    { v.hidden = v.dataset.heroView !== view; });
  // The balance chart may have been drawn while hidden (0-width canvas); resize.
  if (view === "balance" && NH.charts.balance) NH.charts.balance.resize();
}

// ── Autocomplete ────────────────────────────────────────────────────────────────
function nhSuggestions(query) {
  const q = (query || "").trim().toLowerCase();
  const seen = new Set();
  const out = [];
  // Time-of-day patterns first (badged), then previous foods. Merge by name.
  if (!q) {
    NH.freq.forEach((f) => {
      if (seen.has(f.food_name.toLowerCase())) return;
      seen.add(f.food_name.toLowerCase());
      const pf = NH.prev.find((p) => p.food_name === f.food_name);
      out.push({ ...(pf || { food_name: f.food_name, protein: 0, carbs: 0, fat: 0 }), badge: "USUAL" });
    });
  }
  NH.prev.forEach((p) => {
    if (out.length >= 8) return;
    const name = p.food_name.toLowerCase();
    if (seen.has(name)) return;
    if (q && !name.includes(q)) return;
    seen.add(name);
    out.push({ ...p });
  });
  return out.slice(0, 8);
}

// Paint a ready list of suggestion items into the dropdown. Items carry their
// own `mode` so the picker knows how to scale (previous foods = one serving,
// USDA = per-100g). Kept separate from nhRenderSuggest so the async USDA merge
// can repaint the same box without re-deriving the local half.
function nhPaintSuggest(items) {
  const box = document.getElementById("nh-suggest");
  const input = document.getElementById("nh-search");
  if (!box) return;
  if (!items.length) { box.hidden = true; box.innerHTML = ""; if (input) input.setAttribute("aria-expanded", "false"); return; }
  box.innerHTML = items.map((it, i) => {
    const suffix = it.mode === "per100" ? "/100g" : "";
    const macros = `${nhNum(it.protein)}P · ${nhNum(it.carbs)}C · ${nhNum(it.fat)}F${suffix}`;
    const badge = it.badge ? `<span class="nh-sug-badge">${esc(it.badge)}</span>` : "";
    return `<li class="nh-sug" role="option" data-nh-idx="${i}">` +
      `<span class="nh-sug-name">${esc(it.food_name)}${badge}</span>` +
      `<span class="nh-sug-macros">${esc(macros)}</span></li>`;
  }).join("");
  box._items = items;
  box.hidden = false;
  if (input) input.setAttribute("aria-expanded", "true");
}

// Merge USDA search hits (per-100g) after the instant local suggestions,
// deduping by name so a food already in the user's history isn't repeated.
function nhMergeUsda(local, usda) {
  const seen = new Set(local.map((it) => it.food_name.toLowerCase()));
  const out = local.slice();
  (Array.isArray(usda) ? usda : []).forEach((u) => {
    const name = (u.food_name || "").trim();
    if (!name || seen.has(name.toLowerCase())) return;
    seen.add(name.toLowerCase());
    out.push({
      food_name: name,
      protein: Number(u.protein_per_100g) || 0,
      carbs: Number(u.carbs_per_100g) || 0,
      fat: Number(u.fat_per_100g) || 0,
      // USDA household portions (Survey/FNDDS foods) — exact gram weights we
      // surface first in the unit dropdown, ahead of the density-table fallback.
      portions: Array.isArray(u.portions) ? u.portions : [],
      badge: "USDA",
      mode: "per100",
    });
  });
  return out.slice(0, 12);
}

// Monotonic token so a slow USDA response for an old query can't clobber a
// newer one (last-write-wins on the input, not on network arrival order).
let nhSearchSeq = 0;

function nhRenderSuggest(query) {
  const local = nhSuggestions(query);   // instant: previous foods + usuals
  nhPaintSuggest(local);
  const q = (query || "").trim();
  if (q.length < 2) return;             // too short to hit USDA
  const seq = ++nhSearchSeq;
  apiGet(`/api/nutrition/search?q=${encodeURIComponent(q)}`)
    .then((usda) => {
      if (seq !== nhSearchSeq) return;  // a newer query superseded this one
      const input = document.getElementById("nh-search");
      if (!input || input.value.trim().toLowerCase() !== q.toLowerCase()) return;
      nhPaintSuggest(nhMergeUsda(nhSuggestions(query), usda));
    })
    .catch(() => { /* fail soft: keep the local suggestions already shown */ });
}

function nhHideSuggest() {
  const box = document.getElementById("nh-suggest");
  if (box) { box.hidden = true; }
  const input = document.getElementById("nh-search");
  if (input) input.setAttribute("aria-expanded", "false");
}

// ── Serving units (Tier 9b) ──────────────────────────────────────────────────
// Standard unit menu for per-100g foods, in the order the spec calls for:
// USDA portions (prepended per food) → g → household measures. `value` is the
// token the /convert endpoint expects; `label` is what the user sees.
const NH_UNITS = [
  { value: "g", label: "g" },
  { value: "cup", label: "cup" },
  { value: "tbsp", label: "tbsp" },
  { value: "tsp", label: "tsp" },
  { value: "ml", label: "ml" },
  { value: "fl_oz", label: "fl oz" },
  { value: "oz", label: "oz" },
  { value: "piece", label: "piece" },
  { value: "scoop", label: "scoop" },
  { value: "kg", label: "kg" },
  { value: "lb", label: "lb" },
];

// Build the <select> for the picked food: USDA portions first (value
// "portion:<i>", exact gram weight baked into the label), then the standard menu.
function nhFillUnitSelect(portions) {
  const sel = document.getElementById("nh-unit");
  if (!sel) return;
  const opts = [];
  (portions || []).forEach((p, i) => {
    opts.push(`<option value="portion:${i}">${esc(p.label)} · ${nhNum(p.gram_weight)}g</option>`);
  });
  NH_UNITS.forEach((u) => { opts.push(`<option value="${u.value}">${esc(u.label)}</option>`); });
  sel.innerHTML = opts.join("");
}

// Human string for the currently-selected unit, for the meal notes field.
function nhSelectedUnitLabel() {
  const sel = document.getElementById("nh-unit");
  if (!sel) return "";
  const v = sel.value || "";
  if (v.startsWith("portion:")) {
    const p = (NH.picked && NH.picked.portions || [])[parseInt(v.slice(8), 10)];
    return p ? p.label : "";
  }
  const u = NH_UNITS.find((x) => x.value === v);
  return u ? u.label : "";
}

// Show the picked-food box and wire the live macro preview.
// per100 foods get the [amount][unit] pair; previous-foods (serving mode) keep
// the bare servings input unchanged (their macros are already one-serving).
function nhShowPicked(food, mode) {
  NH.picked = {
    food_name: food.food_name,
    protein: Number(food.protein) || 0,
    carbs: Number(food.carbs) || 0,
    fat: Number(food.fat) || 0,
    portions: Array.isArray(food.portions) ? food.portions : [],
    mode: mode || "serving",
  };
  NH.grams = null;            // resolved grams for per100 mode
  NH.estimated = false;
  const box = document.getElementById("nh-picked");
  const name = document.getElementById("nh-picked-name");
  const amount = document.getElementById("nh-amount");
  const unit = document.getElementById("nh-unit");
  if (name) name.innerHTML = `<b>${esc(NH.picked.food_name)}</b>`;
  if (NH.picked.mode === "per100") {
    nhFillUnitSelect(NH.picked.portions);
    if (unit) unit.hidden = false;
    // Default to the first USDA portion (amount 1) if the food carries any,
    // else grams (amount 100) — identical to the old grams-only behaviour.
    if (NH.picked.portions.length) {
      if (unit) unit.value = "portion:0";
      if (amount) { amount.value = "1"; amount.placeholder = "AMOUNT"; }
    } else {
      if (unit) unit.value = "g";
      if (amount) { amount.value = "100"; amount.placeholder = "AMOUNT"; }
    }
  } else {
    if (unit) unit.hidden = true;
    if (amount) { amount.value = "1"; amount.placeholder = "SERVINGS"; }
  }
  if (box) box.hidden = false;
  nhResolvePortion();
}

function nhAmount() {
  const raw = parseFloat(document.getElementById("nh-amount")?.value);
  return (isNaN(raw) || raw <= 0) ? null : raw;
}

// Multiplier applied to the food's stored macros. per100: grams/100 (grams come
// from the resolved conversion); serving: the raw servings number.
function nhScale() {
  if (!NH.picked) return null;
  if (NH.picked.mode === "per100") {
    return (NH.grams != null && NH.grams > 0) ? NH.grams / 100 : null;
  }
  return nhAmount();
}

// Paint the "= 41g" / "~41g (estimated)" readout beneath the amount row.
// Hidden for serving mode and for unit=g (no conversion to show).
function nhPaintGrams() {
  const el = document.getElementById("nh-grams-readout");
  if (!el) return;
  const sel = document.getElementById("nh-unit");
  const unit = sel ? sel.value : "g";
  if (!NH.picked || NH.picked.mode !== "per100" || unit === "g" || NH.grams == null) {
    el.hidden = true; el.textContent = ""; el.classList.remove("nh-grams-est");
    return;
  }
  el.hidden = false;
  el.textContent = NH.estimated ? `~${nhNum(NH.grams)}g (estimated)` : `= ${nhNum(NH.grams)}g`;
  el.classList.toggle("nh-grams-est", NH.estimated);
}

// Resolve the entered amount+unit to grams (per100 mode only), then repaint the
// gram readout and the macro preview. unit=g and USDA portions resolve locally
// (no network); every household measure hits /convert. Monotonic seq guards
// against a slow response for a superseded amount/unit.
let nhConvSeq = 0;
function nhResolvePortion() {
  if (!NH.picked) return;
  if (NH.picked.mode !== "per100") { nhPaintGrams(); nhUpdatePreview(); return; }
  const amt = nhAmount();
  const sel = document.getElementById("nh-unit");
  const unit = sel ? sel.value : "g";
  const done = () => { nhPaintGrams(); nhUpdatePreview(); };
  if (amt == null) { NH.grams = null; NH.estimated = false; done(); return; }
  if (unit === "g") { NH.grams = amt; NH.estimated = false; done(); return; }
  if (unit.startsWith("portion:")) {
    const p = (NH.picked.portions || [])[parseInt(unit.slice(8), 10)];
    NH.grams = p ? Math.round(p.gram_weight * amt * 10) / 10 : null;
    NH.estimated = false; done(); return;
  }
  const seq = ++nhConvSeq;
  apiGet(`/api/nutrition/convert?food=${encodeURIComponent(NH.picked.food_name)}` +
         `&amount=${amt}&unit=${encodeURIComponent(unit)}`)
    .then((d) => {
      if (seq !== nhConvSeq) return;          // superseded by a newer edit
      NH.grams = Number(d.grams);
      NH.estimated = !!d.estimated;
      done();
    })
    .catch(() => { if (seq === nhConvSeq) { NH.grams = null; NH.estimated = false; done(); } });
}

function nhUpdatePreview() {
  const prev = document.getElementById("nh-picked-preview");
  if (!prev || !NH.picked) return;
  const s = nhScale();
  if (s == null) { prev.innerHTML = NH.picked.mode === "per100" ? "ENTER AMOUNT" : "ENTER SERVINGS"; return; }
  const r = (v) => Math.round(v * s * 10) / 10;
  const cals = Math.round((r(NH.picked.protein) * 4 + r(NH.picked.carbs) * 4 + r(NH.picked.fat) * 9));
  prev.innerHTML = `<b>${cals}</b> kcal · ${r(NH.picked.protein)}P · ${r(NH.picked.carbs)}C · ${r(NH.picked.fat)}F`;
}

async function nhLogPicked() {
  const hint = document.getElementById("nh-search-hint");
  const setHint = (m) => { if (hint) hint.textContent = m || ""; };
  setHint("");
  if (!NH.picked) { setHint("PICK A FOOD FIRST"); return; }
  const s = nhScale();
  if (s == null) { setHint(NH.picked.mode === "per100" ? "ENTER AMOUNT" : "ENTER SERVINGS"); return; }
  const r = (v) => Math.round(v * s * 10) / 10;
  const payload = {
    date: NH.date,
    food_name: NH.picked.food_name,
    protein: r(NH.picked.protein),
    carbs: r(NH.picked.carbs),
    fat: r(NH.picked.fat),
    source: "search",
  };
  // Remember the household measure that produced these grams (e.g. "0.5 cup") in
  // the notes field. Grams-direct (unit=g) and serving mode carry nothing extra.
  if (NH.picked.mode === "per100") {
    const label = nhSelectedUnitLabel();
    const amt = nhAmount();
    if (amt != null && label && label !== "g") payload.notes = `${nhNum(amt)} ${label}`;
  }
  const time = document.getElementById("nh-time")?.value.trim();
  payload.time = time || nhNowTime();
  const btn = document.getElementById("nh-search-log");
  if (btn) btn.disabled = true;
  try {
    await apiPost("/api/nutrition/log", payload);
    toast("MEAL LOGGED");
    NH.picked = null;
    const box = document.getElementById("nh-picked"); if (box) box.hidden = true;
    const s2 = document.getElementById("nh-search"); if (s2) s2.value = "";
    nhHideSuggest();
    await fetchNutritionHub();   // refresh suggestions + day
  } catch {
    setHint("LOG FAILED — TRY AGAIN");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function nhNowTime() {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function nhBarcodeLookup() {
  const hint = document.getElementById("nh-search-hint");
  const setHint = (m) => { if (hint) hint.textContent = m || ""; };
  setHint("");
  const code = document.getElementById("nh-barcode-input")?.value.trim();
  if (!code) { setHint("ENTER A BARCODE"); return; }
  const btn = document.getElementById("nh-barcode-lookup");
  if (btn) btn.disabled = true;
  try {
    const d = await apiPost("/api/nutrition/lookup-barcode", { barcode: code });
    if (!d || !d.ok) { setHint("NOT FOUND — TRY SEARCH OR QUICK-ADD"); return; }
    nhShowPicked({
      food_name: d.food_name,
      protein: Number(d.protein_per_100g) || 0,
      carbs: Number(d.carbs_per_100g) || 0,
      fat: Number(d.fat_per_100g) || 0,
    }, "per100");
    setHint("");
  } catch {
    setHint("LOOKUP FAILED — TRY AGAIN");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function nhQuickAdd() {
  const err = document.getElementById("nh-q-err");
  const setErr = (m) => { if (err) err.textContent = m || ""; };
  setErr("");
  const name = document.getElementById("nh-q-name")?.value.trim();
  if (!name) { setErr("ENTER WHAT YOU ATE"); return; }
  const calsRaw = document.getElementById("nh-q-cals")?.value;
  const num = (id) => {
    const raw = document.getElementById(id)?.value;
    if (raw == null || raw === "") return 0;
    const v = parseFloat(raw);
    return isNaN(v) ? NaN : v;
  };
  const protein = num("nh-q-protein"), carbs = num("nh-q-carbs"), fat = num("nh-q-fat");
  if ([protein, carbs, fat].some((v) => isNaN(v) || v < 0)) { setErr("MACROS MUST BE ≥ 0"); return; }
  const payload = { date: NH.date, food_name: name, protein, carbs, fat, source: "quick-add" };
  if (calsRaw != null && calsRaw !== "") {
    const c = parseFloat(calsRaw);
    if (isNaN(c) || c < 0) { setErr("CALORIES MUST BE ≥ 0"); return; }
    payload.calories = c;
  }
  const time = document.getElementById("nh-q-time")?.value.trim();
  payload.time = time || nhNowTime();
  const btn = document.getElementById("nh-q-log");
  if (btn) btn.disabled = true;
  try {
    await apiPost("/api/nutrition/log", payload);
    toast("MEAL LOGGED");
    ["nh-q-name", "nh-q-cals", "nh-q-protein", "nh-q-carbs", "nh-q-fat", "nh-q-time"]
      .forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
    await fetchNutritionHub();
  } catch {
    setErr("LOG FAILED — TRY AGAIN");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function nhUndo() {
  try {
    await apiPost("/api/nutrition/undo", { date: NH.date });
    toast("MEAL REMOVED");
    await fetchNutritionHub();
  } catch { toast("UNDO FAILED"); }
}

// ── Delete any single meal (confirm modal → delete-meal → refresh) ────────────────
let nhPendingDel = null;

function nhConfirmDelete(mealId, foodName) {
  nhPendingDel = mealId;
  const sub = document.getElementById("nh-del-sub");
  if (sub) sub.textContent = `Delete ${foodName || "this meal"}? This can't be undone.`;
  nhModal("nh-del-modal", true);
}

async function nhDoDelete() {
  if (nhPendingDel == null) return;
  const btn = document.getElementById("nh-del-ok");
  if (btn) btn.disabled = true;
  try {
    await apiPost("/api/nutrition/delete-meal", { meal_id: nhPendingDel });
    nhModal("nh-del-modal", false);
    nhPendingDel = null;
    await refreshDay();   // totals · rings · week strip · score all move together
  } catch {
    toast("DELETE FAILED");
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Copy yesterday (relative to the viewed date) ─────────────────────────────────
async function nhOpenCopy() {
  const prevDate = nhShiftDate(NH.date, -1);
  const list = document.getElementById("nh-copy-list");
  const sub = document.getElementById("nh-copy-sub");
  let meals = [];
  try { meals = (await apiGet(`/api/nutrition/date/${prevDate}`)).meals || []; }
  catch { toast("COULDN'T LOAD PREVIOUS DAY"); return; }
  if (!meals.length) { toast("NOTHING TO COPY FROM " + nhDateLabel(prevDate).toUpperCase()); return; }
  if (sub) sub.textContent = `Log meals from ${nhDateLabel(prevDate)} into ${nhDateLabel(NH.date)}.`;
  if (list) {
    list.innerHTML = meals.map((m, i) =>
      `<li class="nh-copy-item"><label>` +
      `<input type="checkbox" data-nh-copy="${i}" checked> ` +
      `<span class="nm-name">${esc(m.food_name)}</span>` +
      `<span class="nm-macros">${nhNum(m.protein)}P · ${nhNum(m.carbs)}C · ${nhNum(m.fat)}F</span>` +
      `</label></li>`).join("");
    list._meals = meals;
  }
  nhModal("nh-copy-modal", true);
}

async function nhConfirmCopy() {
  const list = document.getElementById("nh-copy-list");
  const meals = (list && list._meals) || [];
  const picks = [...document.querySelectorAll('[data-nh-copy]')]
    .filter((cb) => cb.checked).map((cb) => meals[parseInt(cb.dataset.nhCopy, 10)]);
  if (!picks.length) { nhModal("nh-copy-modal", false); return; }
  const btn = document.getElementById("nh-copy-confirm");
  if (btn) btn.disabled = true;
  let ok = 0;
  for (const m of picks) {
    try {
      await apiPost("/api/nutrition/log", {
        date: NH.date, food_name: m.food_name,
        protein: nhNum(m.protein), carbs: nhNum(m.carbs), fat: nhNum(m.fat),
        time: m.time || undefined, source: "quick-add",
      });
      ok++;
    } catch { /* skip the failed one, keep going */ }
  }
  if (btn) btn.disabled = false;
  nhModal("nh-copy-modal", false);
  toast(`LOGGED ${ok} MEAL${ok === 1 ? "" : "S"}`);
  await fetchNutritionHub();
}

// ── Goals modal ──────────────────────────────────────────────────────────────────
function nhOpenGoals() {
  const g = NH.goals || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = (v == null ? "" : v); };
  set("nh-goal-protein", g.protein_goal);
  set("nh-goal-carbs", g.carbs_goal);
  set("nh-goal-fat", g.fat_goal);
  set("nh-goal-cals", g.calorie_goal);
  const err = document.getElementById("nh-goals-err"); if (err) err.textContent = "";
  nhModal("nh-goals-modal", true);
}

async function nhSaveGoals() {
  const err = document.getElementById("nh-goals-err");
  const setErr = (m) => { if (err) err.textContent = m || ""; };
  const val = (id) => parseFloat(document.getElementById(id)?.value);
  const protein = val("nh-goal-protein"), carbs = val("nh-goal-carbs"),
        fat = val("nh-goal-fat"), calories = val("nh-goal-cals");
  if ([protein, carbs, fat, calories].some((v) => isNaN(v) || v < 0)) {
    setErr("GOALS MUST BE NUMBERS ≥ 0"); return;
  }
  const btn = document.getElementById("nh-goals-save");
  if (btn) btn.disabled = true;
  try {
    const d = await apiPost("/api/nutrition/goals",
      { protein_goal: protein, carbs_goal: carbs, fat_goal: fat, calorie_goal: calories });
    NH.goals = d.goals || NH.goals;
    toast("GOALS SAVED");
    nhModal("nh-goals-modal", false);
    await refreshDay();
  } catch {
    setErr("SAVE FAILED — TRY AGAIN");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function nhModal(id, open) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("open", !!open);
  el.setAttribute("aria-hidden", open ? "false" : "true");
}

function nhSwitchPane(name) {
  document.querySelectorAll(".nh-tab").forEach((t) =>
    t.classList.toggle("active", t.dataset.nhTab === name));
  document.querySelectorAll(".nh-pane").forEach((p) =>
    { p.hidden = p.dataset.nhPane !== name; });
}

function wireNutritionHub() {
  const root = document.getElementById("nutrition-screen");
  if (!root) return;
  NH.date = nhToday();

  // Date navigation — full refresh so week strip · rings · score · charts move.
  const prev = document.getElementById("nh-date-prev");
  const next = document.getElementById("nh-date-next");
  if (prev) prev.addEventListener("click", () => { NH.date = nhShiftDate(NH.date, -1); fetchNutritionHub(); });
  if (next) next.addEventListener("click", () => { NH.date = nhShiftDate(NH.date, 1); fetchNutritionHub(); });

  // Hero carousel dots
  document.querySelectorAll(".nh-dot").forEach((d) =>
    d.addEventListener("click", () => nhSwitchHero(d.dataset.heroView)));

  // Week strip — column click/enter navigates the whole page to that day.
  const week = document.getElementById("nh-week");
  if (week) {
    const nav = (e) => {
      const col = e.target.closest("[data-nh-day]"); if (!col) return;
      goToDate(col.dataset.nhDay);
    };
    week.addEventListener("click", nav);
    week.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); nav(e); } });
  }

  // Favorites pills — one-tap re-log (delegated; row is re-rendered each refresh).
  const favs = document.getElementById("nh-favorites");
  if (favs) favs.addEventListener("click", (e) => {
    const pill = e.target.closest("[data-nh-fav]"); if (pill) nhLogFavorite(pill.dataset.nhFav);
  });

  // Templates row — log an existing template (confirm) or open the new-template modal.
  const tpls = document.getElementById("nh-templates");
  if (tpls) tpls.addEventListener("click", (e) => {
    if (e.target.closest("#nh-new-template")) { nhOpenTemplateModal(null); return; }
    const pill = e.target.closest("[data-nh-tpl]"); if (pill) nhConfirmLogTemplate(pill.dataset.nhTpl);
  });

  // Template modal save + close; log-template confirm.
  const tplSave = document.getElementById("nh-template-save");
  if (tplSave) tplSave.addEventListener("click", nhSaveTemplate);
  const tplClose = document.getElementById("nh-template-close");
  if (tplClose) tplClose.addEventListener("click", () => nhModal("nh-template-modal", false));
  const tplOk = document.getElementById("nh-tpl-confirm-ok");
  if (tplOk) tplOk.addEventListener("click", nhDoLogTemplate);
  const tplCfClose = document.getElementById("nh-tpl-confirm-close");
  if (tplCfClose) tplCfClose.addEventListener("click", () => { nhPendingTpl = null; nhModal("nh-tpl-confirm-modal", false); });

  // Entry tabs
  document.querySelectorAll(".nh-tab").forEach((t) =>
    t.addEventListener("click", () => nhSwitchPane(t.dataset.nhTab)));

  // Search autocomplete (debounced)
  const search = document.getElementById("nh-search");
  if (search) {
    let deb = null;
    search.addEventListener("input", () => {
      clearTimeout(deb);
      deb = setTimeout(() => nhRenderSuggest(search.value), 300);
    });
    search.addEventListener("focus", () => nhRenderSuggest(search.value));
  }
  const suggest = document.getElementById("nh-suggest");
  if (suggest) suggest.addEventListener("click", (e) => {
    const li = e.target.closest("[data-nh-idx]"); if (!li) return;
    const items = suggest._items || [];
    const it = items[parseInt(li.dataset.nhIdx, 10)];
    if (!it) return;
    nhHideSuggest();
    if (search) search.value = it.food_name;
    // Previous-food macros are absolute for one serving (serving mode); USDA
    // hits are per-100g and carry mode:"per100" so grams scale correctly.
    nhShowPicked(it, it.mode || "serving");
  });
  // Dismiss suggestions when focus leaves the search area.
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#nh-search") && !e.target.closest("#nh-suggest")) nhHideSuggest();
  });

  // Portion preview — amount typing is debounced (250ms) because non-g units hit
  // /convert; unit change resolves immediately (portions/g are local anyway).
  const amount = document.getElementById("nh-amount");
  if (amount) {
    let adeb = null;
    amount.addEventListener("input", () => {
      clearTimeout(adeb);
      adeb = setTimeout(nhResolvePortion, 250);
    });
  }
  const unitSel = document.getElementById("nh-unit");
  if (unitSel) unitSel.addEventListener("change", nhResolvePortion);
  const searchLog = document.getElementById("nh-search-log");
  if (searchLog) searchLog.addEventListener("click", nhLogPicked);

  // Barcode toggle + lookup
  const bcBtn = document.getElementById("nh-barcode-btn");
  const bcBox = document.getElementById("nh-barcode-box");
  if (bcBtn && bcBox) bcBtn.addEventListener("click", () => { bcBox.hidden = !bcBox.hidden; });
  const bcLookup = document.getElementById("nh-barcode-lookup");
  if (bcLookup) bcLookup.addEventListener("click", nhBarcodeLookup);

  // Quick-add
  const qLog = document.getElementById("nh-q-log");
  if (qLog) qLog.addEventListener("click", nhQuickAdd);

  // Undo + save-as-template (delegated on the meal list)
  const meals = document.getElementById("nh-meals");
  if (meals) meals.addEventListener("click", (e) => {
    const del = e.target.closest("[data-nh-del]");
    if (del) { nhConfirmDelete(del.dataset.nhDel, del.dataset.nhFood); return; }
    if (e.target.closest("[data-nh-undo]")) { nhUndo(); return; }
    const save = e.target.closest("[data-nh-save-tpl]");
    if (save) nhOpenTemplateModal(save.dataset.nhSaveTpl);
  });

  // Quick actions
  const copyBtn = document.getElementById("nh-copy-yday");
  if (copyBtn) copyBtn.addEventListener("click", nhOpenCopy);
  const copyConfirm = document.getElementById("nh-copy-confirm");
  if (copyConfirm) copyConfirm.addEventListener("click", nhConfirmCopy);
  const copyClose = document.getElementById("nh-copy-close");
  if (copyClose) copyClose.addEventListener("click", () => nhModal("nh-copy-modal", false));

  const goalsBtn = document.getElementById("nh-goals-btn");
  if (goalsBtn) goalsBtn.addEventListener("click", nhOpenGoals);
  const goalsSave = document.getElementById("nh-goals-save");
  if (goalsSave) goalsSave.addEventListener("click", nhSaveGoals);
  const goalsClose = document.getElementById("nh-goals-close");
  if (goalsClose) goalsClose.addEventListener("click", () => nhModal("nh-goals-modal", false));

  // Delete-meal confirm modal
  const delOk = document.getElementById("nh-del-ok");
  if (delOk) delOk.addEventListener("click", nhDoDelete);
  const closeDel = () => { nhPendingDel = null; nhModal("nh-del-modal", false); };
  const delCancel = document.getElementById("nh-del-cancel");
  if (delCancel) delCancel.addEventListener("click", closeDel);
  const delClose = document.getElementById("nh-del-close");
  if (delClose) delClose.addEventListener("click", closeDel);

  // Click the dim backdrop to close any modal.
  ["nh-copy-modal", "nh-goals-modal", "nh-template-modal", "nh-tpl-confirm-modal", "nh-del-modal"].forEach((id) => {
    const ov = document.getElementById(id);
    if (ov) ov.addEventListener("click", (e) => { if (e.target === ov) nhModal(id, false); });
  });
}

/* ── Page init ────────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", function () {
  wireNutritionHub();
  fetchNutritionHub();
});

})();
