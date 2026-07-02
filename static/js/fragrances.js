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

function toast(msg, ms = 2200) {
  const t = el("div", "frag-toast", esc(msg));
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

const ROUTINE_STEPS = [
  ["shower_gel",  "🚿", "Wash"],
  ["body_scrub",  "🧽", "Scrub"],
  ["body_lotion", "🧴", "Lotion"],
  ["body_oil",    "💧", "Oil"],
  ["deodorant",   "🛡", "Deo"],
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
  for (const [key, icon, label] of ROUTINE_STEPS) {
    const p = routine && routine[key];
    if (!p) continue;
    steps.push(`<div class="routine-step"><span class="rs-icon">${icon}</span>
      <span class="rs-text"><b>${esc(p.brand)}</b> ${esc(p.name)}</span>
      <span class="rs-label mono">${label}</span></div>`);
  }
  if (routine && routine.layering_fragrance) {
    steps.push(`<div class="routine-step rs-layer"><span class="rs-icon">↻</span>
      <span class="rs-text"><b>Layer:</b> ${esc(routine.layering_fragrance.name)}
      ${routine.layering_notes ? `<small class="muted-sub"> — ${esc(routine.layering_notes)}</small>` : ""}</span></div>`);
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
        <div class="hero-name">${f.is_signature ? "⭐ " : ""}${esc(f.name)}</div>
        <div class="hero-brand mono">${esc(f.brand)} · ${esc(f.concentration || "")}</div>
        <p class="hero-reason">${esc(rec.reason)}</p>
        <div class="hero-routine">
          <div class="routine-head mono">FULL ROUTINE</div>
          ${routineChecklist(rec.routine)}
          <div class="routine-step rs-final"><span class="rs-icon">💨</span>
            <span class="rs-text"><b>${esc(f.name)}</b></span>
            <span class="rs-label mono">Spray</span></div>
        </div>
        <button class="btn btn-primary hero-wear" id="hero-wear-btn">✓ Wear this</button>
      </div>
    </div>`;
  $("#hero-wear-btn").addEventListener("click", () => wearFragrance(f.id, $("#hero-wear-btn")));
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

/* Optimistic one-tap wear logging (undo covers mis-taps — no dialogs). */
async function wearFragrance(id, btn, occasion) {
  if (btn) btn.disabled = true;
  try {
    const updated = await apiPost(`${API}/${id}/wear`, {
      time_of_day: timeBucket(),
      occasion: occasion || OCCASION || undefined,
    });
    confettiLite();
    toast(`💨 ${updated.name} logged — smell great out there`);
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
        <div class="bottle-name">${f.is_signature ? '<span class="sig-star" title="Signature">⭐</span> ' : ""}${esc(f.name)}</div>
        <div class="bottle-brand mono">${esc(f.brand)}</div>
        <div class="bottle-chips">
          <span class="chip chip-conc">${esc(f.concentration || "?")}</span>
          <span class="chip chip-count" title="Total wears">${f.wear_count || 0}×</span>
          <span class="chip ${neglected ? "chip-neglect" : "chip-worn"}">${esc(wornAgo(f.days_since_worn))}</span>
        </div>
      </div>
      <button class="bottle-cam" title="Upload bottle photo" aria-label="Upload photo of ${esc(f.name)}">📷</button>
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
      : "none 🎉";
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
        <div class="fd-name">${f.is_signature ? "⭐ " : ""}${esc(f.name)}</div>
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
    await wearFragrance(f.id, $("#fd-wear-btn"));
    openDetail(f.id);
  });
  $("#fd-undo-btn").addEventListener("click", () => undoWear(f.id));
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
    toast("📷 Photo saved");
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
document.addEventListener("DOMContentLoaded", () => {
  initPills();
  initDetailClose();
  refreshShelf().catch(() => toast("Could not load collection"));
  loadHero();
  loadStats();
});
})();
