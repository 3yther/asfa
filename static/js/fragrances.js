/* ══════════════════════════════════════════════════════════════════════════
   fragrances.js — Scent Vault frontend for ASFA.
   Vanilla JS, self-contained (own api helpers so the standalone /fragrances
   page never depends on main.js). CSRF on writes comes from the global fetch
   wrapper in _csrf.html. Sections:
     0. helpers          3. detail drawer
     1. hero + pills     4. image upload
     2. shelf            5. confetti-lite
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
"use strict";

/* ── 0. Helpers ───────────────────────────────────────────────────────── */
const API = "/api/fragrances";
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
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

/* Inline monochrome camera icon (currentColor) — replaces the camera emoji on
   the per-bottle upload button so the shelf reads clean. */
const IC_CAMERA = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3"/></svg>';

function toast(msg, ms = 2200) {
  const t = el("div", "frag-toast", esc(msg));
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

/* Tier 3 Part 2: rate a worn pairing up/down. Recommendations learn from the
   last few ratings (net nudge, never an override). */
async function ratePairing(pairingId, rating) {
  const res = await apiPost(`${API}/pairings/${pairingId}/rate`, { rating });
  return res && res.net;  // updated clamped net (-3..+3)
}

/* Optional up/down prompt after a wear that used a pairing (auto-dismisses). */
function ratingPrompt(pairingId, name) {
  if (!pairingId) return;
  const t = el("div", "frag-toast frag-rate-toast");
  t.innerHTML =
    `<span class="frt-q">Rate today's ${esc(name)} routine?</span>
     <div class="frag-rate-btns">
       <button class="frag-rate-btn" data-r="1" aria-label="rate up">+</button>
       <button class="frag-rate-btn" data-r="-1" aria-label="rate down">−</button>
     </div>`;
  document.body.appendChild(t);
  let done = false;
  const dismiss = setTimeout(() => { if (!done) t.remove(); }, 6000);
  t.querySelectorAll(".frag-rate-btn").forEach(b => b.addEventListener("click", async () => {
    done = true; clearTimeout(dismiss);
    t.querySelectorAll(".frag-rate-btn").forEach(x => x.disabled = true);
    try {
      await ratePairing(pairingId, Number(b.dataset.r));
      t.querySelector(".frt-q").textContent = b.dataset.r === "1" ? "Rated up" : "Rated down";
    } catch (e) { t.querySelector(".frt-q").textContent = "Couldn't save rating"; }
    setTimeout(() => t.remove(), 1200);
  }));
}

const ROUTINE_STEPS = [
  ["shower_gel",  "Wash"],
  ["body_scrub",  "Scrub"],
  ["body_lotion", "Lotion"],
  ["body_oil",    "Oil"],
  ["deodorant",   "Deo"],
];

const timeBucket = () => {
  const h = new Date().getHours();
  return h >= 5 && h < 11 ? "morning" : h < 17 ? "day" : h < 22 ? "evening" : "night";
};
const wornAgo = (d) =>
  d == null ? "never worn" : d === 0 ? "worn today" : d === 1 ? "worn 1d ago" : `worn ${d}d ago`;

let FRAGS = [];           // shelf cache
let OCCASION = "";        // selected pill
let CURRENT_REC = null;   // last recommendation payload

/* ── 1. Hero recommendation + occasion pills ─────────────────────────── */
async function loadHero() {
  const body = $("#frag-hero-body");
  try {
    const url = OCCASION ? `${API}/recommendation?occasion=${encodeURIComponent(OCCASION)}` : `${API}/recommendation`;
    const rec = await apiGet(url);
    CURRENT_REC = rec;
    renderHero(rec);
  } catch (e) {
    body.innerHTML = `<div class="muted">// RECOMMENDATION UNAVAILABLE</div>`;
  }
}

function heroTitle(bucket) {
  return bucket === "evening" || bucket === "night" ? "Tonight's Scent" : "Today's Scent";
}

function contextLine(ctx) {
  const bits = [ctx.time_bucket ? ctx.time_bucket[0].toUpperCase() + ctx.time_bucket.slice(1) : ""];
  if (ctx.temp_c != null) bits.push(`${Math.round(ctx.temp_c)}°C`);
  if (ctx.condition) bits.push(String(ctx.condition).toLowerCase());
  return bits.filter(Boolean).join(" · ");
}

function routineChecklist(routine, compact) {
  const steps = [];
  for (const [key, label] of ROUTINE_STEPS) {
    const p = routine && routine[key];
    if (!p) continue;
    steps.push(`<div class="routine-step">
      <span class="rs-text"><b>${esc(p.brand)}</b> ${esc(p.name)}</span>
      <span class="rs-label mono">${label}</span></div>`);
  }
  if (routine && routine.layering_fragrance) {
    steps.push(`<div class="routine-step rs-layer">
      <span class="rs-text"><b>Layer:</b> ${esc(routine.layering_fragrance.name)}
      ${routine.layering_notes ? `<small class="muted-sub"> — ${esc(routine.layering_notes)}</small>` : ""}</span>
      <span class="rs-label mono">Layer</span></div>`);
  }
  return steps.join("") || `<div class="muted-sub">No routine on file.</div>`;
}

function bottleArt(frag, cls) {
  if (frag.image_url) {
    return `<img class="${cls}" src="${esc(frag.image_url)}" alt="${esc(frag.name)}" loading="lazy">`;
  }
  const initial = esc((frag.brand || "?").trim().charAt(0).toUpperCase());
  return `<div class="${cls} bottle-placeholder"><span class="bp-monogram" aria-hidden="true">${initial}</span></div>`;
}

function renderHero(rec) {
  const f = rec.fragrance, ctx = rec.context || {};
  $("#frag-hero-title").textContent = heroTitle(ctx.time_bucket);
  $("#frag-hero-context").textContent = contextLine(ctx) || "—";
  const body = $("#frag-hero-body");
  body.innerHTML = `
    <div class="hero-grid">
      <div class="hero-bottle">${bottleArt(f, "hero-img")}</div>
      <div class="hero-main">
        <div class="hero-name">${f.is_signature ? "★ " : ""}${esc(f.name)}</div>
        <div class="hero-brand mono">${esc(f.brand)} · ${esc(f.concentration || "")}</div>
        <p class="hero-reason">${esc(rec.reason)}</p>
        <div class="hero-routine">
          <div class="routine-head mono">FULL ROUTINE</div>
          ${routineChecklist(rec.routine)}
          <div class="routine-step rs-final">
            <span class="rs-text"><b>${esc(f.name)}</b></span>
            <span class="rs-label mono">Spray</span></div>
        </div>
        <button class="btn btn-primary hero-wear" id="hero-wear-btn">✓ Wear this</button>
      </div>
    </div>`;
  $("#hero-wear-btn").addEventListener("click", () =>
    wearFragrance(f.id, $("#hero-wear-btn"), rec.context && rec.context.occasion,
                  rec.routine && rec.routine.id));
}

function initPills() {
  const wrap = $("#frag-occasions");
  wrap.querySelector('[data-occasion=""]').classList.add("active");
  wrap.addEventListener("click", (e) => {
    const pill = e.target.closest(".occ-pill");
    if (!pill) return;
    OCCASION = pill.dataset.occasion;
    wrap.querySelectorAll(".occ-pill").forEach(p => p.classList.toggle("active", p === pill));
    loadHero();
  });
}

/* Optimistic one-tap wear logging (undo covers mis-taps — no dialogs).
   pairingId (when the wear used a curated routine) triggers an optional 👍/👎. */
async function wearFragrance(id, btn, occasion, pairingId) {
  if (btn) btn.disabled = true;
  try {
    const updated = await apiPost(`${API}/${id}/wear`, {
      time_of_day: timeBucket(),
      occasion: occasion || OCCASION || undefined,
    });
    confettiLite();
    toast(`${updated.name} logged — smell great out there`);
    if (pairingId) ratingPrompt(pairingId, updated.name);
    await refreshShelf();
    loadStats();
  } catch (e) {
    toast("Could not log wear");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function undoWear(id) {
  try {
    const updated = await apiDel(`${API}/${id}/wear/last`);
    toast(`Undone — ${updated.name} back to ${updated.wear_count} wears`);
    await refreshShelf();
    loadStats();
    openDetail(id); // re-render the drawer with fresh numbers
  } catch (e) {
    toast("Nothing to undo");
  }
}

/* ── 2. Shelf ─────────────────────────────────────────────────────────── */
async function refreshShelf() {
  FRAGS = await apiGet(API);
  renderShelf();
}

function renderShelf() {
  const shelf = $("#frag-shelf");
  shelf.innerHTML = "";
  FRAGS.forEach(f => {
    const neglected = f.days_since_worn == null || f.days_since_worn > 30;
    const card = el("div", "bottle-card", `
      <div class="bottle-stage">
        ${bottleArt(f, "bottle-img")}
        <div class="bottle-reflection"></div>
      </div>
      <div class="bottle-meta">
        <div class="bottle-name">${f.is_signature ? '<span class="sig-star" title="Signature">★</span> ' : ""}${esc(f.name)}</div>
        <div class="bottle-brand mono">${esc(f.brand)}</div>
        <div class="bottle-chips">
          <span class="chip chip-conc">${esc(f.concentration || "?")}</span>
          <span class="chip chip-count" title="Total wears">${f.wear_count || 0}×</span>
          <span class="chip ${neglected ? "chip-neglect" : "chip-worn"}">${esc(wornAgo(f.days_since_worn))}</span>
        </div>
      </div>
      <button class="bottle-cam" title="Upload bottle photo" aria-label="Upload photo of ${esc(f.name)}">${IC_CAMERA}</button>
      <div class="bottle-upload-progress" hidden><div class="bup-bar"></div></div>`);
    card.addEventListener("click", (e) => {
      if (e.target.closest(".bottle-cam")) { pickImage(f.id, card); return; }
      openDetail(f.id);
    });
    shelf.appendChild(card);
  });
}

/* ── Stats strip ──────────────────────────────────────────────────────── */
async function loadStats() {
  const strip = $("#frag-stats-strip");
  try {
    const s = await apiGet(`${API}/stats`);
    const most = s.most_worn ? `${esc(s.most_worn.name)} (${s.most_worn.wear_count}×)` : "—";
    const negl = s.neglected.length
      ? esc(s.neglected.map(n => n.days_since_worn == null
          ? `${n.name} (never)` : `${n.name} (${n.days_since_worn}d)`).slice(0, 2).join(", "))
      : "none";
    const maxShare = Math.max(...s.rotation.map(r => r.share), 0);
    const bars = s.rotation.map(r => {
      const h = maxShare ? Math.max(8, Math.round((r.share / maxShare) * 100)) : 8;
      return `<div class="rb-bar" style="height:${h}%" title="${esc(r.name)}: ${r.wear_count} wears"></div>`;
    }).join("");
    strip.innerHTML = `
      <div class="fs-item"><span class="fs-num mono glow">${s.wears_this_month}</span><span class="fs-label">wears this month</span></div>
      <div class="fs-item"><span class="fs-val">${most}</span><span class="fs-label">most worn</span></div>
      <div class="fs-item ${s.neglected.length ? "fs-warn" : ""}"><span class="fs-val">${negl}</span><span class="fs-label">neglected (&gt;30d)</span></div>
      <div class="fs-item fs-rotation"><div class="rotation-bars" aria-label="Rotation balance">${bars}</div><span class="fs-label">rotation balance</span></div>`;
  } catch (e) {
    strip.innerHTML = `<div class="muted">// STATS UNAVAILABLE</div>`;
  }
}

/* ── 3. Detail drawer ─────────────────────────────────────────────────── */
function parsePyramid(notes) {
  const tiers = { Top: "", Heart: "", Base: "" };
  String(notes || "").split(";").forEach(part => {
    const m = part.match(/\s*(Top|Heart|Base)\s*:\s*(.+)/i);
    if (m) tiers[m[1][0].toUpperCase() + m[1].slice(1).toLowerCase()] = m[2].trim();
  });
  return tiers;
}

function wearCalendar(wears) {
  // 90-day dot grid, oldest → today; filled where a wear row exists.
  const byDate = {};
  (wears || []).forEach(w => { byDate[w.date] = w; });
  const cells = [];
  for (let i = 89; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const iso = d.toISOString().slice(0, 10);
    const w = byDate[iso];
    const tip = w ? `${iso}${w.occasion ? " · " + w.occasion : ""}${w.time_of_day ? " · " + w.time_of_day : ""}` : iso;
    cells.push(`<span class="wc-dot ${w ? "worn" : ""}" title="${esc(tip)}"></span>`);
  }
  return `<div class="wear-cal">${cells.join("")}</div>`;
}

async function openDetail(id) {
  const overlay = $("#frag-detail-overlay");
  const body = $("#frag-detail-body");
  overlay.hidden = false;
  document.body.classList.add("frag-noscroll");
  body.innerHTML = `<div class="skeleton skeleton-block"></div>`;
  let f;
  try { f = await apiGet(`${API}/${id}`); }
  catch (e) { body.innerHTML = `<div class="muted">// COULD NOT LOAD</div>`; return; }
  const tiers = parsePyramid(f.notes);
  const seasons = String(f.best_seasons || "").split(",").filter(Boolean);
  const vibes = String(f.vibe || "").split(",").filter(Boolean);
  body.innerHTML = `
    <div class="fd-top">
      <div class="fd-img-wrap">${bottleArt(f, "fd-img")}</div>
      <div class="fd-id">
        <div class="fd-name">${f.is_signature ? "★ " : ""}${esc(f.name)}</div>
        <div class="fd-brand mono">${esc(f.brand)}</div>
        <div class="bottle-chips">
          <span class="chip chip-conc">${esc(f.concentration || "?")}</span>
          ${f.longevity_hrs ? `<span class="chip">~${f.longevity_hrs}h</span>` : ""}
          <span class="chip">${esc(String(f.time_of_day || ""))}</span>
        </div>
        <div class="fd-tags">${vibes.map(v => `<span class="tag">${esc(v.trim())}</span>`).join("")}</div>
        <div class="fd-seasons mono">${seasons.map(s => `<span class="season">${esc(s.trim())}</span>`).join(" ")}</div>
      </div>
    </div>
    <div class="fd-pyramid">
      ${["Top", "Heart", "Base"].map(t => `
        <div class="pyr-tier pyr-${t.toLowerCase()}">
          <span class="pyr-label mono">${t}</span>
          <span class="pyr-notes">${esc(tiers[t] || "—")}</span>
        </div>`).join("")}
    </div>
    <div class="fd-routine">
      <div class="routine-head mono">RECOMMENDED ROUTINE</div>
      ${routineChecklist(f.pairing)}
      ${f.pairing && f.pairing.reason ? `<p class="fd-reason muted-sub">${esc(f.pairing.reason)}</p>` : ""}
      ${f.pairing && f.pairing.id ? `
      <div class="fd-rate">
        <span>Rate this combo:</span>
        <button class="frag-rate-btn${f.pairing.rating_net > 0 ? " frb-active" : ""}" data-r="1" aria-label="rate up">+</button>
        <button class="frag-rate-btn${f.pairing.rating_net < 0 ? " frb-active" : ""}" data-r="-1" aria-label="rate down">−</button>
        <span class="fd-rate-net" id="fd-rate-net">${f.pairing.rating_net > 0 ? "+" : ""}${f.pairing.rating_net || 0}</span>
      </div>` : ""}
      <button class="btn btn-primary" id="fd-wear-btn">✓ Wear with this routine</button>
    </div>
    <div class="fd-cal">
      <div class="routine-head mono">LAST 90 DAYS</div>
      ${wearCalendar(f.wears)}
      <button class="fd-undo" id="fd-undo-btn" type="button">Undo last wear</button>
    </div>
    <div class="fd-stats mono">
      <span>${f.wear_count || 0} total wears</span>
      <span>${esc(wornAgo(f.days_since_worn))}</span>
      <span>#${f.rotation_rank || "—"} of ${f.collection_size} in rotation</span>
    </div>`;
  $("#fd-wear-btn").addEventListener("click", async () => {
    await wearFragrance(f.id, $("#fd-wear-btn"), undefined, f.pairing && f.pairing.id);
    openDetail(f.id);
  });
  $("#fd-undo-btn").addEventListener("click", () => undoWear(f.id));
  // Inline 👍/👎 in the drawer (Tier 3 Part 2) — update the net in place.
  if (f.pairing && f.pairing.id) {
    body.querySelectorAll(".fd-rate .frag-rate-btn").forEach(b =>
      b.addEventListener("click", async () => {
        try {
          const net = await ratePairing(f.pairing.id, Number(b.dataset.r));
          const netEl = $("#fd-rate-net");
          if (netEl) netEl.textContent = (net > 0 ? "+" : "") + (net || 0);
          body.querySelectorAll(".fd-rate .frag-rate-btn").forEach(x => x.classList.remove("frb-active"));
          if (net > 0) body.querySelector('.fd-rate .frag-rate-btn[data-r="1"]').classList.add("frb-active");
          else if (net < 0) body.querySelector('.fd-rate .frag-rate-btn[data-r="-1"]').classList.add("frb-active");
          toast(b.dataset.r === "1" ? "Rated up" : "Rated down");
        } catch (e) { toast("Couldn't save rating"); }
      }));
  }
}

function initDetailClose() {
  const overlay = $("#frag-detail-overlay");
  const close = () => { overlay.hidden = true; document.body.classList.remove("frag-noscroll"); };
  $("#frag-detail-close").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !overlay.hidden) close(); });
}

/* ── 4. Image upload (magic bytes + size validated server-side too) ──── */
const input = document.getElementById("frag-upload-input");
let uploadTarget = null; // { id, card }

function pickImage(id, card) {
  uploadTarget = { id, card };
  input.value = "";
  input.click();
}

input && input.addEventListener("change", async () => {
  const file = input.files && input.files[0];
  if (!file || !uploadTarget) return;
  if (file.size > 5 * 1024 * 1024) { toast("Image too large (max 5MB)"); return; }
  const { id, card } = uploadTarget;
  const prog = card.querySelector(".bottle-upload-progress");
  const bar = card.querySelector(".bup-bar");
  prog.hidden = false;
  try {
    const fd = new FormData();
    fd.append("image", file);
    // XHR (not fetch) purely for upload progress events. The CSRF token is
    // added by hand here because the _csrf.html wrapper only patches fetch.
    const imageUrl = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${API}/${id}/image`);
      xhr.withCredentials = true;
      const token = document.querySelector("meta[name=csrf-token]");
      if (token) xhr.setRequestHeader("X-CSRF-Token", token.content);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && bar) bar.style.width = `${Math.round((e.loaded / e.total) * 100)}%`;
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText).image_url);
        else {
          let msg = "upload failed";
          try { msg = JSON.parse(xhr.responseText).error || msg; } catch (e2) {}
          reject(new Error(msg));
        }
      };
      xhr.onerror = () => reject(new Error("network error"));
      xhr.send(fd);
    });
    // Swap the placeholder instantly, then re-sync the caches.
    const frag = FRAGS.find(x => x.id === id);
    if (frag) frag.image_url = imageUrl;
    renderShelf();
    loadHero();
    toast("Photo saved");
  } catch (e) {
    toast(`Upload failed: ${e.message}`);
  } finally {
    prog.hidden = true;
    if (bar) bar.style.width = "0%";
    uploadTarget = null;
  }
});

/* ── 5. Confetti-lite (a few glowing flecks, no library) ─────────────── */
function confettiLite() {
  const colors = ["#00d9ff", "#d4af37", "#7f77dd", "#5fe6ff"];
  for (let i = 0; i < 18; i++) {
    const p = el("span", "frag-confetti");
    p.style.background = colors[i % colors.length];
    p.style.left = `${45 + Math.random() * 10}%`;
    p.style.setProperty("--dx", `${(Math.random() - 0.5) * 240}px`);
    p.style.setProperty("--dy", `${-120 - Math.random() * 220}px`);
    p.style.animationDelay = `${Math.random() * 0.12}s`;
    document.body.appendChild(p);
    setTimeout(() => p.remove(), 1400);
  }
}

/* ── boot ─────────────────────────────────────────────────────────────── */
/* ── 6. Add fragrance — FragDB name autocomplete + prefill (Tier 3 Part 6) ── */
function initAddFragrance() {
  const overlay = $("#frag-add-overlay"), openBtn = $("#frag-add-btn");
  if (!overlay || !openBtn) return;
  const form = $("#frag-add-form"), ac = $("#fa-ac"), nameEl = $("#fa-name");
  let items = [], idx = -1, timer = null;

  const hideAc = () => { ac.hidden = true; ac.innerHTML = ""; items = []; idx = -1; };
  const open = () => { overlay.hidden = false; document.body.classList.add("frag-noscroll"); nameEl.focus(); };
  const close = () => { overlay.hidden = true; document.body.classList.remove("frag-noscroll"); hideAc(); };
  openBtn.addEventListener("click", open);
  $("#frag-add-close").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !overlay.hidden) close(); });

  function render(list) {
    items = list; idx = -1;
    if (!list.length) { hideAc(); return; }
    ac.innerHTML = list.map((it, i) =>
      `<div class="fa-ac-item" data-i="${i}"><div class="fa-ac-name">${esc(it.name)}</div>
       <div class="fa-ac-brand">${esc(it.brand || "")}${it.concentration ? " · " + esc(it.concentration) : ""}</div></div>`).join("");
    ac.hidden = false;
  }
  function highlight() { ac.querySelectorAll(".fa-ac-item").forEach((e, i) => e.classList.toggle("active", i === idx)); }
  function pick(i) {
    const it = items[i]; if (!it) return;
    nameEl.value = it.name || "";
    $("#fa-brand").value = it.brand || "";
    $("#fa-conc").value = it.concentration || "";
    $("#fa-notes").value = it.notes || "";
    if (it.accords && !$("#fa-vibe").value) $("#fa-vibe").value = String(it.accords).toLowerCase();
    hideAc();
  }
  ac.addEventListener("click", (e) => { const it = e.target.closest(".fa-ac-item"); if (it) pick(Number(it.dataset.i)); });
  nameEl.addEventListener("input", () => {
    const q = nameEl.value.trim();
    clearTimeout(timer);
    if (q.length < 2) { hideAc(); return; }
    timer = setTimeout(async () => {
      try { render(await apiGet(`${API}/reference/search?q=${encodeURIComponent(q)}`)); }
      catch (e) { hideAc(); }
    }, 180);
  });
  nameEl.addEventListener("keydown", (e) => {
    if (ac.hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); idx = Math.min(idx + 1, items.length - 1); highlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); idx = Math.max(idx - 1, 0); highlight(); }
    else if (e.key === "Enter" && idx >= 0) { e.preventDefault(); pick(idx); }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const val = (id) => $(id).value.trim();
    const body = { name: nameEl.value.trim(), brand: val("#fa-brand"), concentration: val("#fa-conc"),
      notes: val("#fa-notes"), vibe: val("#fa-vibe"), best_seasons: val("#fa-seasons"),
      time_of_day: val("#fa-time"), occasions: val("#fa-occ") };
    if (!body.name || !body.brand) { toast("Name and brand required"); return; }
    const btn = $("#fa-save"); btn.disabled = true;
    try {
      const frag = await apiPost(API, body);
      toast(`Added ${frag.name}`);
      form.reset(); close();
      await refreshShelf(); loadStats();
    } catch (err) { toast("Could not add bottle"); }
    finally { btn.disabled = false; }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initPills();
  initDetailClose();
  initAddFragrance();
  refreshShelf().catch(() => toast("Could not load collection"));
  loadHero();
  loadStats();
});
})();
