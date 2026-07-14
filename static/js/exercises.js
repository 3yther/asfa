/* ══════════════════════════════════════════════════════════════════════════
   exercises.js — Exercise Library browser for ASFA (/gym/exercises).
   Vanilla JS, self-contained. Reads the synced catalogue via /api/exercises,
   renders a filtered/paginated GIF grid + detail modal, and hands a chosen
   exercise to the gym session via a localStorage queue drained by gym.js.
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
"use strict";

/* ── Helpers ──────────────────────────────────────────────────────────── */
async function apiGet(url) {
  const r = await fetch(url, { credentials: "include" });
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
async function apiPost(url, body) {
  const opts = { method: "POST", credentials: "include" };
  if (body !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}
const $  = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
const LS_PENDING = "gym_pending_adds";   // shared handoff key with gym.js

let toastTimer = null;
function toast(msg) {
  const t = $("#exl-toast");
  t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
}

/* ── State ────────────────────────────────────────────────────────────── */
const state = {
  category: "", difficulty: "", home_only: false, q: "",
  equipment: new Set(), page: 1,
};
let lastMeta = { total: 0, pages: 0, page: 1 };

function buildQuery() {
  const p = new URLSearchParams();
  if (state.category) p.set("category", state.category);
  if (state.difficulty) p.set("difficulty", state.difficulty);
  if (state.home_only) p.set("home_only", "1");
  if (state.q) p.set("q", state.q);
  if (state.equipment.size) p.set("equipment", Array.from(state.equipment).join(","));
  p.set("page", state.page);
  return p.toString();
}

/* ── Filter sidebar ───────────────────────────────────────────────────── */
async function loadFacets() {
  let f;
  try { f = await apiGet("/api/exercises/facets"); }
  catch (e) { return; }
  const cat = $("#exl-category");
  (f.categories || []).forEach(c => {
    const o = document.createElement("option"); o.value = c;
    o.textContent = c.replace(/\b\w/g, m => m.toUpperCase()); cat.appendChild(o);
  });
  const diffSel = $("#exl-difficulty");
  const diffs = (f.difficulties || []);
  if (diffs.length) {
    diffs.forEach(d => {
      const o = document.createElement("option"); o.value = d;
      o.textContent = d.replace(/\b\w/g, m => m.toUpperCase()); diffSel.appendChild(o);
    });
  } else {
    // difficulty is curated later; hide the control until values exist.
    diffSel.closest(".exl-fgroup").hidden = true;
  }
  const eqWrap = $("#exl-equipment");
  eqWrap.innerHTML = "";
  (f.equipment || []).forEach(eq => {
    const lab = document.createElement("label"); lab.className = "exl-check";
    lab.innerHTML = `<input type="checkbox" value="${esc(eq)}"><span>${esc(eq)}</span>`;
    lab.querySelector("input").addEventListener("change", (e) => {
      if (e.target.checked) state.equipment.add(eq); else state.equipment.delete(eq);
      state.page = 1; fetchList();
    });
    eqWrap.appendChild(lab);
  });
}

/* ── Results grid ─────────────────────────────────────────────────────── */
function card(ex) {
  const c = document.createElement("div");
  c.className = "card sci-fi-panel exl-card";
  c.dataset.id = ex.id;
  const badges = [];
  if (ex.target_muscle) badges.push(`<span class="exl-badge target">${esc(ex.target_muscle)}</span>`);
  if (ex.equipment) badges.push(`<span class="exl-badge">${esc(ex.equipment)}</span>`);
  if (ex.is_home_friendly) badges.push(`<span class="exl-badge home">Home</span>`);
  c.innerHTML = `
    <div class="exl-gifwrap">
      ${ex.gif_url ? `<img class="exl-gif" loading="lazy" alt="${esc(ex.name)} demo" src="${esc(ex.gif_url)}">` : ""}
    </div>
    <div class="exl-cardbody">
      <div class="exl-name">${esc(ex.name)}</div>
      <div class="exl-meta">${badges.join("")}</div>
    </div>`;
  c.addEventListener("click", () => openDetail(ex));
  return c;
}

async function fetchList() {
  const grid = $("#exl-grid");
  grid.innerHTML = `<div class="exl-loading">Loading exercises…</div>`;
  let data;
  try { data = await apiGet("/api/exercises?" + buildQuery()); }
  catch (e) {
    grid.innerHTML = `<div class="exl-empty">Could not load exercises — are you logged in?</div>`;
    return;
  }
  lastMeta = { total: data.total, pages: data.pages, page: data.page };
  const rows = data.exercises || [];
  $("#exl-count").textContent =
    `${data.total.toLocaleString()} exercise${data.total === 1 ? "" : "s"}`;
  if (!rows.length) {
    grid.innerHTML = `<div class="exl-empty">No exercises match these filters.</div>`;
  } else {
    grid.innerHTML = "";
    rows.forEach(ex => grid.appendChild(card(ex)));
  }
  const pager = $("#exl-pager");
  pager.hidden = data.pages <= 1;
  $("#exl-pageinfo").textContent = `Page ${data.page} / ${data.pages}`;
  $("#exl-prev").disabled = data.page <= 1;
  $("#exl-next").disabled = data.page >= data.pages;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ── Detail modal ─────────────────────────────────────────────────────── */
async function openDetail(exLite) {
  const overlay = $("#exl-overlay"), body = $("#exl-modal-body"), gif = $("#exl-modal-gif");
  overlay.classList.add("open");
  gif.src = exLite.gif_url || ""; gif.alt = exLite.name || "";
  body.innerHTML = `<div class="exl-loading">Loading…</div>`;
  let ex = exLite;
  try { ex = await apiGet("/api/exercises/" + encodeURIComponent(exLite.id)); }
  catch (e) { /* fall back to the list row */ }

  const badges = [];
  if (ex.category) badges.push(`<span class="exl-badge">${esc(ex.category)}</span>`);
  if (ex.target_muscle) badges.push(`<span class="exl-badge target">${esc(ex.target_muscle)}</span>`);
  if (ex.equipment) badges.push(`<span class="exl-badge">${esc(ex.equipment)}</span>`);
  if (ex.is_home_friendly) badges.push(`<span class="exl-badge home">Home-friendly</span>`);

  const steps = (ex.instructions || "")
    .split(/(?<=[.!?])\s+(?=[A-Z0-9])/).map(s => s.trim()).filter(Boolean);
  const stepsHtml = steps.length
    ? `<ol class="exl-steps">${steps.map(s => `<li>${esc(s)}</li>`).join("")}</ol>`
    : `<p class="exl-alt">No instructions available for this exercise.</p>`;

  body.innerHTML = `
    <div class="exl-modal-title">${esc(ex.name)}</div>
    <div class="exl-modal-meta">${badges.join("")}</div>
    <h4>Instructions</h4>
    ${stepsHtml}
    <div class="exl-alt" id="exl-alt">
      ${ex.is_home_friendly
        ? `<strong>Home-friendly.</strong> No gym equipment needed — do it anywhere.`
        : `Needs ${esc(ex.equipment || "equipment")}. Looking for a home option…`}
    </div>
    <button class="exl-add" id="exl-add">＋ Add to workout</button>`;

  $("#exl-add").addEventListener("click", () => addToWorkout(ex, $("#exl-add")));
  if (!ex.is_home_friendly && ex.category) loadHomeAlternative(ex);
}

// Suggest a home-friendly exercise in the same category as a substitute.
async function loadHomeAlternative(ex) {
  try {
    const d = await apiGet(`/api/exercises?home_only=1&category=${encodeURIComponent(ex.category)}&per_page=8`);
    const alt = (d.exercises || []).find(a => a.id !== ex.id);
    const box = $("#exl-alt");
    if (!box) return;
    if (alt) {
      box.innerHTML = `<strong>Home alternative:</strong> ` +
        `<a data-alt="${esc(alt.id)}">${esc(alt.name)}</a> — same ${esc(ex.category)} muscle group, no equipment.`;
      box.querySelector("a").addEventListener("click", () => openDetail(alt));
    } else {
      box.textContent = `Needs ${ex.equipment || "equipment"}. No home-friendly ${ex.category} alternative found.`;
    }
  } catch (e) { /* leave the placeholder text */ }
}

async function addToWorkout(ex, btn) {
  btn.disabled = true;
  try {
    const res = await apiPost(`/api/exercises/${encodeURIComponent(ex.id)}/add-to-workout`);
    // Queue the loggable gym_exercise; gym.js drains it into the active session.
    let q;
    try { q = JSON.parse(localStorage.getItem(LS_PENDING) || "[]"); } catch (e) { q = []; }
    if (!Array.isArray(q)) q = [];
    const gx = res.gym_exercise;
    if (gx && !q.some(e => e.id === gx.id)) q.push(gx);
    localStorage.setItem(LS_PENDING, JSON.stringify(q));
    btn.textContent = "✓ Added to workout";
    toast(`${ex.name} queued for your next workout — open Iron Log to log sets.`);
  } catch (e) {
    btn.disabled = false;
    toast("Could not add — are you logged in?");
  }
}

function closeModal() { $("#exl-overlay").classList.remove("open"); }

/* ── Wiring ───────────────────────────────────────────────────────────── */
function debounce(fn, ms) {
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function init() {
  $("#exl-category").addEventListener("change", (e) => {
    state.category = e.target.value; state.page = 1; fetchList();
  });
  $("#exl-difficulty").addEventListener("change", (e) => {
    state.difficulty = e.target.value; state.page = 1; fetchList();
  });
  $("#exl-home").addEventListener("change", (e) => {
    state.home_only = e.target.checked; state.page = 1; fetchList();
  });
  $("#exl-search").addEventListener("input", debounce((e) => {
    state.q = e.target.value.trim(); state.page = 1; fetchList();
  }, 280));
  $("#exl-reset").addEventListener("click", () => {
    state.category = state.difficulty = state.q = ""; state.home_only = false;
    state.equipment.clear(); state.page = 1;
    $("#exl-category").value = ""; $("#exl-difficulty").value = "";
    $("#exl-home").checked = false; $("#exl-search").value = "";
    document.querySelectorAll("#exl-equipment input").forEach(i => (i.checked = false));
    fetchList();
  });
  $("#exl-prev").addEventListener("click", () => {
    if (state.page > 1) { state.page--; fetchList(); }
  });
  $("#exl-next").addEventListener("click", () => {
    if (state.page < lastMeta.pages) { state.page++; fetchList(); }
  });
  $("#exl-modal-close").addEventListener("click", closeModal);
  $("#exl-overlay").addEventListener("click", (e) => {
    if (e.target === $("#exl-overlay")) closeModal();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  loadFacets();
  fetchList();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();

})();
