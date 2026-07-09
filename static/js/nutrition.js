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

function nhRenderReadouts(totals, goals) {
  const g = goals || {};
  const t = totals || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  const left = (goal, used) => Math.max(0, Math.round((Number(goal) || 0) - (Number(used) || 0)));
  const pct = (used, goal) => {
    const gg = Number(goal) || 0; if (gg <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((Number(used) || 0) / gg * 100)));
  };
  set("nh-cals-left", left(g.calorie_goal, t.total_calories) + " kcal");
  set("nh-protein-left", left(g.protein_goal, t.total_protein));
  set("nh-carbs-left", left(g.carbs_goal, t.total_carbs));
  set("nh-fat-left", left(g.fat_goal, t.total_fat));
  const bar = (id, used, goal) => {
    const el = document.getElementById(id); if (el) el.style.width = pct(used, goal) + "%";
  };
  bar("nh-cals-bar", t.total_calories, g.calorie_goal);
  bar("nh-protein-bar", t.total_protein, g.protein_goal);
  bar("nh-carbs-bar", t.total_carbs, g.carbs_goal);
  bar("nh-fat-bar", t.total_fat, g.fat_goal);
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
    html += `<div class="nh-meal-group"><div class="hud-label nh-group-head">${b}</div>`;
    rows.forEach((m) => {
      const macros = `${nhNum(m.protein)}P · ${nhNum(m.carbs)}C · ${nhNum(m.fat)}F`;
      const undo = Number(m.id) === lastId
        ? `<button type="button" class="nh-undo" data-nh-undo aria-label="Undo last meal" title="Undo last">↶</button>`
        : "";
      html += `<div class="nh-meal">` +
        `<span class="nm-name">${esc(m.food_name)}</span>` +
        `<span class="nm-macros">${esc(macros)}</span>${undo}</div>`;
    });
    html += `</div>`;
  });
  wrap.innerHTML = html;
}

function nhRenderDay(day) {
  if (!day) return;
  NH.goals = day.goals || NH.goals;
  nhRenderReadouts(day.totals, NH.goals);
  nhRenderMeals(day.meals);
  const label = document.getElementById("nh-date");
  if (label) { label.textContent = nhDateLabel(NH.date); label.dataset.date = NH.date; }
}

async function fetchNutritionHub() {
  const card = document.getElementById("nutrition-hub");
  if (!card) return;
  if (!NH.date) NH.date = nhToday();
  const hour = new Date().getHours();
  const [goals, day, prev, freq] = await Promise.allSettled([
    apiGet("/api/nutrition/goals"),
    apiGet(`/api/nutrition/date/${NH.date}`),
    apiGet("/api/nutrition/previous-foods?limit=50"),
    apiGet(`/api/nutrition/frequent-at-hour?hour=${hour}&limit=5`),
  ]);
  if (goals.status === "fulfilled") NH.goals = goals.value;
  if (prev.status === "fulfilled" && Array.isArray(prev.value)) NH.prev = prev.value;
  if (freq.status === "fulfilled" && Array.isArray(freq.value)) NH.freq = freq.value;
  if (day.status === "fulfilled") {
    nhRenderDay(day.value);
  } else {
    // Never blank: show goals with zero consumed as a fallback.
    nhRenderReadouts({}, NH.goals);
    nhRenderMeals([]);
    const label = document.getElementById("nh-date");
    if (label) label.textContent = nhDateLabel(NH.date);
  }
}

async function nhReloadDay() {
  try {
    const day = await apiGet(`/api/nutrition/date/${NH.date}`);
    nhRenderDay(day);
  } catch { /* keep current view */ }
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

// Show the picked-food box and wire the live macro preview.
function nhShowPicked(food, mode) {
  NH.picked = {
    food_name: food.food_name,
    protein: Number(food.protein) || 0,
    carbs: Number(food.carbs) || 0,
    fat: Number(food.fat) || 0,
    mode: mode || "serving",
  };
  const box = document.getElementById("nh-picked");
  const name = document.getElementById("nh-picked-name");
  const grams = document.getElementById("nh-grams");
  if (name) name.innerHTML = `<b>${esc(NH.picked.food_name)}</b>`;
  if (grams) {
    if (NH.picked.mode === "per100") { grams.value = "100"; grams.placeholder = "HOW MANY GRAMS?"; }
    else { grams.value = "1"; grams.placeholder = "SERVINGS"; }
  }
  if (box) box.hidden = false;
  nhUpdatePreview();
}

function nhScale() {
  const raw = parseFloat(document.getElementById("nh-grams")?.value);
  if (isNaN(raw) || raw <= 0) return null;
  return NH.picked && NH.picked.mode === "per100" ? raw / 100 : raw;
}
function nhUpdatePreview() {
  const prev = document.getElementById("nh-picked-preview");
  if (!prev || !NH.picked) return;
  const s = nhScale();
  if (s == null) { prev.innerHTML = NH.picked.mode === "per100" ? "ENTER GRAMS" : "ENTER SERVINGS"; return; }
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
  if (s == null) { setHint(NH.picked.mode === "per100" ? "ENTER GRAMS" : "ENTER SERVINGS"); return; }
  const r = (v) => Math.round(v * s * 10) / 10;
  const payload = {
    date: NH.date,
    food_name: NH.picked.food_name,
    protein: r(NH.picked.protein),
    carbs: r(NH.picked.carbs),
    fat: r(NH.picked.fat),
    source: "search",
  };
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
    await nhReloadDay();
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
  const card = document.getElementById("nutrition-hub");
  if (!card) return;
  NH.date = nhToday();

  // Date navigation
  const prev = document.getElementById("nh-date-prev");
  const next = document.getElementById("nh-date-next");
  if (prev) prev.addEventListener("click", () => { NH.date = nhShiftDate(NH.date, -1); nhReloadDay(); });
  if (next) next.addEventListener("click", () => { NH.date = nhShiftDate(NH.date, 1); nhReloadDay(); });

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

  // Portion preview
  const grams = document.getElementById("nh-grams");
  if (grams) grams.addEventListener("input", nhUpdatePreview);
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

  // Undo (delegated on the meal list)
  const meals = document.getElementById("nh-meals");
  if (meals) meals.addEventListener("click", (e) => {
    if (e.target.closest("[data-nh-undo]")) nhUndo();
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

  // Click the dim backdrop to close either modal.
  ["nh-copy-modal", "nh-goals-modal"].forEach((id) => {
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
